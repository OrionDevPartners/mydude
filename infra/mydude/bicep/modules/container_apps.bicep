// MyDude — Container Apps (BCS Gate, Master_DB, Fan-out Gateway)

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object
param acaSubnetId string
param bcsGateIdentityId string
param readonlyIdentityId string
param keyVaultName string
param logAnalyticsWorkspaceId string
param unityCatalogEndpoint string      // Databricks workspace URL; BCS gate hard-fails if absent
param databricksSqlWarehouseId string  // SQL Warehouse ID for DDL + INSERT via Statement Execution API

resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-aca-env'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: acaSubnetId
      internal: false
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(logAnalyticsWorkspaceId, '2022-10-01').customerId
        sharedKey: listKeys(logAnalyticsWorkspaceId, '2022-10-01').primarySharedKey
      }
    }
  }
}

// BCS Gate — single truth writer, min-1, uses bcsGateIdentity exclusively
resource bcsGate 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-bcs-gate'
  location: location
  tags: union(tags, { role: 'truth-writer', authority: 'bcs-promotion-gate' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${bcsGateIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: false
        targetPort: 8080
        transport: 'http'
      }
      secrets: [
        // Full DSN connection strings (not passwords) — constructed by the provisioning
        // script from host + port + user + password and stored as complete strings.
        // Format: postgresql://user:password@host:5432/agents_home?sslmode=require
        {
          name: 'pg-agents-home-dsn'
          keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/agents-home-pg-dsn'
          identity: bcsGateIdentityId
        }
        // Full DSN for provider_home (BCS gate reads provider outbox for replay)
        // Format: postgresql://user:password@host:5432/provider_home?sslmode=require
        {
          name: 'pg-provider-home-dsn'
          keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/provider-home-pg-dsn'
          identity: bcsGateIdentityId
        }
        {
          name: 'bcs-lease-secret'
          keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/bcs-gate-idempotency-key'
          identity: bcsGateIdentityId
        }
      ]
    }
    template: {
      scale: {
        minReplicas: 1
        maxReplicas: 5
        rules: [
          {
            name: 'http-scale'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
      containers: [
        {
          name: 'bcs-gate'
          image: 'mydude/bcs-gate:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            // Full DSN strings (not passwords) — Postgres advisory locks require a real connection
            { name: 'PG_AGENTS_HOME_DSN', secretRef: 'pg-agents-home-dsn' }
            { name: 'PG_PROVIDER_HOME_DSN', secretRef: 'pg-provider-home-dsn' }
            { name: 'BCS_LEASE_SECRET', secretRef: 'bcs-lease-secret' }
            { name: 'SCOPE_GATE_VERSION', value: 'V7' }
            { name: 'MANAGED_IDENTITY_CLIENT_ID', value: reference(bcsGateIdentityId, '2023-01-31').clientId }
            // Unity Catalog endpoint — required for BCS gate to write claims.
            // BCS gate startup checks this and refuses to start if absent (PRODUCTION_MODE=true).
            { name: 'UNITY_CATALOG_ENDPOINT', value: unityCatalogEndpoint }
            // SQL Warehouse required for: CREATE CATALOG/SCHEMA/TABLE (bootstrap) and INSERT (claim writes).
            // Uses the Databricks SQL Statement Execution API — the Unity Catalog metadata REST API
            // is for object discovery only, not DDL execution or row-level writes.
            { name: 'DATABRICKS_SQL_WAREHOUSE_ID', value: databricksSqlWarehouseId }
            { name: 'PRODUCTION_MODE', value: 'true' }
          ]
        }
      ]
    }
  }
}

// Master_DB — DuckDB in Container App (catalog read-only, fan-out aggregation)
resource masterDb 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-master-db'
  location: location
  tags: union(tags, { role: 'query-aggregator', authority: 'none' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${readonlyIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: false
        targetPort: 8081
        transport: 'http'
      }
    }
    template: {
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
      containers: [
        {
          name: 'master-db'
          image: 'mydude/master-db:latest'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            { name: 'CATALOG_READ_ONLY', value: 'true' }
            { name: 'MANAGED_IDENTITY_CLIENT_ID', value: reference(readonlyIdentityId, '2023-01-31').clientId }
          ]
        }
      ]
    }
  }
}

// Fan-out Gateway — external-facing, catalog read-only
resource fanoutGateway 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-fanout-gw'
  location: location
  tags: union(tags, { role: 'query-gateway', authority: 'none' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${readonlyIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8082
        transport: 'http'
      }
    }
    template: {
      scale: {
        minReplicas: 1
        maxReplicas: 10
        rules: [
          {
            name: 'http-scale'
            http: { metadata: { concurrentRequests: '100' } }
          }
        ]
      }
      containers: [
        {
          name: 'fanout-gw'
          image: 'mydude/fanout-gateway:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'CATALOG_READ_ONLY', value: 'true' }
            { name: 'MASTER_DB_URL', value: 'http://${prefix}-master-db' }
            { name: 'MANAGED_IDENTITY_CLIENT_ID', value: reference(readonlyIdentityId, '2023-01-31').clientId }
          ]
        }
      ]
    }
  }
}

output bcsGateInternalUrl string = 'https://${bcsGate.properties.configuration.ingress.fqdn}'
output masterDbInternalUrl string = 'https://${masterDb.properties.configuration.ingress.fqdn}'
output fanoutGatewayExternalUrl string = 'https://${fanoutGateway.properties.configuration.ingress.fqdn}'
