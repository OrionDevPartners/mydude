// MyDude — Azure AI Search (rebuildable projection — not an authority)
// Rebuilt from Unity Catalog on demand. Never writes to the catalog.

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object
param readonlyPrincipalId string

resource aiSearch 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: '${prefix}-search'
  location: location
  tags: union(tags, { role: 'projection', authority: 'none' })
  sku: {
    name: 'standard'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'disabled'
    semanticSearch: 'free'
    encryptionWithCmk: {
      enforcement: 'Unspecified'
    }
  }
}

// The readonly principal gets Search Index Data Reader
resource searchReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiSearch.id, readonlyPrincipalId, 'SearchIndexDataReader')
  scope: aiSearch
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
    principalId: readonlyPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output searchEndpoint string = 'https://${aiSearch.name}.search.windows.net'
output searchId string = aiSearch.id
