// MyDude — Azure Cosmos DB (NoSQL) for the agent swarm memory (document + vector).
//
// AUTHORITY: Cosmos holds the agents' working memory — episodic events, semantic
// vectors, and raw documents. Postgres remains the relational/governance/audit/secrets
// authority; Fabric/OneLake holds the large domain-knowledge corpus. Cosmos is the
// low-latency agent memory tier and replaces Azure AI Search (vector lives here + pgvector).
//
// SECURITY: private-endpoint only (publicNetworkAccess Disabled), key auth disabled
// (disableLocalAuth) — all access via AAD/Cosmos data-plane RBAC.

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object
param peSubnetId string
param cosmosPrivateDnsZoneId string

@description('Principal that reads+writes agent memory (Cosmos built-in Data Contributor).')
param agentDataContributorPrincipalId string

@description('Principal with read-only data-plane access (Cosmos built-in Data Reader).')
param readonlyPrincipalId string

@description('Autoscale MAX RU/s for the shared agents-memory database (scales 10% .. max).')
param cosmosMaxThroughput int = 10000

@description('Dedicated autoscale MAX RU/s for the vectors container. Vector (diskANN) indexing is NOT supported on a shared/database-level throughput offer, so this container provisions its own dedicated throughput.')
param vectorsContainerMaxThroughput int = 4000

@description('Vector dimensions for the embeddings stored in the vectors container.')
param vectorDimensions int = 1536

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: '${prefix}-cosmos'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
    enableAutomaticFailover: true
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: true
      }
    ]
    capabilities: [
      { name: 'EnableNoSQLVectorSearch' }
    ]
    backupPolicy: {
      type: 'Continuous'
      continuousModeProperties: {
        tier: 'Continuous7Days'
      }
    }
  }
}

// Shared-throughput database for agent memory (autoscale)
resource db 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmos
  name: 'agents_memory'
  properties: {
    resource: {
      id: 'agents_memory'
    }
    options: {
      autoscaleSettings: {
        maxThroughput: cosmosMaxThroughput
      }
    }
  }
}

// Episodic memory — per-agent event/interaction log
resource episodic 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: db
  name: 'episodic'
  properties: {
    resource: {
      id: 'episodic'
      partitionKey: {
        paths: ['/agentId']
        kind: 'Hash'
      }
      defaultTtl: -1
    }
  }
}

// Semantic memory — vector-indexed (diskANN) for similarity recall
resource vectors 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: db
  name: 'vectors'
  properties: {
    resource: {
      id: 'vectors'
      partitionKey: {
        paths: ['/namespace']
        kind: 'Hash'
      }
      vectorEmbeddingPolicy: {
        vectorEmbeddings: [
          {
            path: '/embedding'
            dataType: 'float32'
            distanceFunction: 'cosine'
            dimensions: vectorDimensions
          }
        ]
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/embedding/*' }
        ]
        vectorIndexes: [
          {
            path: '/embedding'
            type: 'diskANN'
          }
        ]
      }
    }
    // Dedicated throughput — vector indexing is rejected on the shared database
    // offer ("Vector Indexing is not supported for shared throughput offer").
    options: {
      autoscaleSettings: {
        maxThroughput: vectorsContainerMaxThroughput
      }
    }
  }
}

// Documents — raw agent documents / working context
resource documents 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: db
  name: 'documents'
  properties: {
    resource: {
      id: 'documents'
      partitionKey: {
        paths: ['/namespace']
        kind: 'Hash'
      }
    }
  }
}

// Data-plane RBAC — built-in Cosmos data roles, account-scoped (no key auth).
// 00000000-...-0002 = Cosmos DB Built-in Data Contributor
resource dataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, agentDataContributorPrincipalId, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: agentDataContributorPrincipalId
    scope: cosmos.id
  }
}

// 00000000-...-0001 = Cosmos DB Built-in Data Reader
resource dataReader 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, readonlyPrincipalId, 'cosmos-data-reader')
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000001'
    principalId: readonlyPrincipalId
    scope: cosmos.id
  }
}

// Private endpoint (groupId 'Sql') — sole access path from the VNet
resource cosmosPe 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${prefix}-cosmos-pe'
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [
      {
        name: '${prefix}-cosmos-plsc'
        properties: {
          privateLinkServiceId: cosmos.id
          groupIds: ['Sql']
        }
      }
    ]
  }
}

resource cosmosDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: cosmosPe
  name: 'cosmosDnsGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-documents'
        properties: {
          privateDnsZoneId: cosmosPrivateDnsZoneId
        }
      }
    ]
  }
}

output cosmosAccountName string = cosmos.name
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output cosmosId string = cosmos.id
output databaseName string = db.name
