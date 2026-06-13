// MyDude — ADLS Gen2 storage (knowledge-raw lake landing, OneLake staging, LanceDB L2, MLflow artifacts, offline-sync)

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object
param bcsGatePrincipalId string
param readonlyPrincipalId string
param peSubnetId string
param vnetId string
param storageDnsZoneId string

resource adls 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${prefix}stg'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_RAGRS'
  }
  properties: {
    isHnsEnabled: true
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      virtualNetworkRules: [
        { id: '${vnetId}/subnets/mydude-pg-subnet' }
      ]
    }
    encryption: {
      services: {
        blob: { enabled: true }
        file: { enabled: true }
      }
      keySource: 'Microsoft.Storage'
    }
  }
}

// Containers (ADLS filesystem containers)
resource knowledgeRawContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${adls.name}/default/knowledge-raw'
  properties: {
    publicAccess: 'None'
  }
}

resource onelakeStagingContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${adls.name}/default/onelake-staging'
  properties: {
    publicAccess: 'None'
  }
}

resource lancedbL2Container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${adls.name}/default/lancedb-l2'
  properties: {
    publicAccess: 'None'
  }
}

resource mlflowContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${adls.name}/default/mlflow-artifacts'
  properties: {
    publicAccess: 'None'
  }
}

resource offlineSyncContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${adls.name}/default/offline-sync'
  properties: {
    publicAccess: 'None'
  }
}

// RBAC: BCS gate = Storage Blob Data Contributor (write authority for the lake landing zone)
resource bcsGateStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(adls.id, bcsGatePrincipalId, 'StorageBlobDataContributor')
  scope: adls
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: bcsGatePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// RBAC: readonly identity = Storage Blob Data Reader
resource readonlyStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(adls.id, readonlyPrincipalId, 'StorageBlobDataReader')
  scope: adls
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
    principalId: readonlyPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Private endpoint for ADLS
resource adlsPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${prefix}-storage-pe'
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [
      {
        name: '${prefix}-storage-plsc'
        properties: {
          privateLinkServiceId: adls.id
          groupIds: ['dfs']
        }
      }
    ]
  }
}

// DNS zone group — routes <account>.dfs.core.windows.net to private IP inside the VNet.
resource adlsDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: adlsPrivateEndpoint
  name: 'adlsDnsGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-dfs'
        properties: {
          privateDnsZoneId: storageDnsZoneId
        }
      }
    ]
  }
}

output adlsAccountName string = adls.name
output adlsId string = adls.id
output knowledgeRawUri string = 'abfss://knowledge-raw@${adls.name}.dfs.core.windows.net/'
output onelakeStagingUri string = 'abfss://onelake-staging@${adls.name}.dfs.core.windows.net/'
output lancedbL2Uri string = 'abfss://lancedb-l2@${adls.name}.dfs.core.windows.net/'
output mlflowArtifactsUri string = 'abfss://mlflow-artifacts@${adls.name}.dfs.core.windows.net/'
