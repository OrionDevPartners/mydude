// MyDude — Azure MCP Dev Accelerator
// A VNet-internal Azure Container App that runs the governed FastMCP server
// (src/mcp/azure_dev_server.py). Ingress is internal-only (external:false); the
// bearer token is sourced from Key Vault at runtime under the supplied
// user-assigned identity, never injected as a Bicep secret (pillar #3).
//
// The aca-subnet (10.10.1.0/24) is already delegated to Microsoft.App/environments
// in network.bicep and hosts this managed environment's infrastructure.

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object

@description('Resource id of the VNet subnet delegated to Microsoft.App/environments.')
param acaSubnetId string

@description('Name of the Log Analytics workspace the managed environment streams logs to.')
param logAnalyticsWorkspaceName string

@description('User-assigned identity (resource id) the container runs as — needs Key Vault get plus the data-plane RBAC for the tools it exposes (Cosmos read, Postgres, AOAI).')
param userAssignedIdentityId string

@description('Fully-qualified container image, e.g. myregistry.azurecr.io/mydude-azure-mcp:2026-06-14. REQUIRED.')
param containerImage string

@description('Container registry login server for managed-identity pull, e.g. myregistry.azurecr.io. REQUIRED.')
param containerRegistryServer string

@description('Azure subscription id the server resolves ARM outputs / Key Vault from.')
param subscriptionId string = subscription().subscriptionId

@description('Key Vault secret name holding the MCP bearer token (the value lives only in Key Vault).')
param authSecretName string = 'azure-mcp-auth-token'

@description('Key Vault secret name holding the two-phase deploy-token SIGNING secret (the value lives only in Key Vault; the container fetches it at runtime under its identity). Required for azure_deploy_plan/apply to mint+verify plan tokens.')
param deployTokenSecretName string = 'azure-mcp-deploy-token-secret'

@description('Enable the BILLABLE two-phase deploy APPLY tool. Default false (default-deny).')
param enableAzureDeploy bool = false

@description('Container CPU cores.')
param cpu string = '0.5'

@description('Container memory.')
param memory string = '1Gi'

@description('Min replicas (set 0 to allow scale-to-zero).')
param minReplicas int = 1

@description('Max replicas.')
param maxReplicas int = 2

// Existing Log Analytics workspace — read its customerId + shared key for the
// managed environment's log sink. The key stays inside the ARM template plane.
resource la 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

// User-assigned identity (existing) — referenced for its clientId so
// DefaultAzureCredential inside the container binds to the right MI.
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(userAssignedIdentityId, '/'))
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-mcp-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: la.properties.customerId
        sharedKey: la.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: acaSubnetId
      internal: true
    }
    zoneRedundant: false
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-azure-mcp'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: containerRegistryServer
          identity: userAssignedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'azure-mcp'
          image: containerImage
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            { name: 'AZURE_SUBSCRIPTION_ID', value: subscriptionId }
            { name: 'AZURE_CLIENT_ID', value: uami.properties.clientId }
            { name: 'ENABLE_AZURE_MCP', value: 'true' }
            { name: 'ALLOW_AZURE_DEPLOY', value: enableAzureDeploy ? 'true' : 'false' }
            { name: 'AZURE_MCP_AUTH_SECRET_NAME', value: authSecretName }
            // NAME only (never the value): the server fetches the deploy-token
            // signing secret from Key Vault at runtime under its UAMI (pillar #3),
            // so the plan->apply token binding survives restarts/replicas.
            { name: 'AZURE_MCP_DEPLOY_SECRET_NAME', value: deployTokenSecretName }
            { name: 'AZURE_MCP_PORT', value: '8080' }
            { name: 'AZURE_MCP_HOST', value: '0.0.0.0' }
            // Internal ingress + bearer auth + the private VNet already guard the
            // endpoint; the app FQDN is only known post-deploy, so DNS-rebinding
            // host pinning is disabled here. Tighten by setting
            // AZURE_MCP_ALLOWED_HOSTS to the app FQDN after the first deploy.
            { name: 'AZURE_MCP_DISABLE_HOST_CHECK', value: 'true' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8080 }
              initialDelaySeconds: 15
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: { path: '/healthz', port: 8080 }
              initialDelaySeconds: 10
              periodSeconds: 15
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output managedEnvironmentName string = env.name
output containerAppName string = app.name
output containerAppFqdn string = app.properties.configuration.ingress.fqdn
output containerAppInternalUrl string = 'https://${app.properties.configuration.ingress.fqdn}/mcp'
