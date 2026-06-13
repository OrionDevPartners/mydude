// MyDude — VNet, subnets, private endpoints, DNS zones, Azure Policy

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: '${prefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: ['10.10.0.0/16']
    }
    subnets: [
      {
        name: '${prefix}-aca-subnet'
        properties: {
          addressPrefix: '10.10.1.0/24'
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: '${prefix}-pg-subnet'
        properties: {
          addressPrefix: '10.10.2.0/24'
          delegations: [
            {
              name: 'pg-delegation'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
          serviceEndpoints: [
            { service: 'Microsoft.Storage' }
          ]
        }
      }
      {
        name: '${prefix}-pe-subnet'
        properties: {
          addressPrefix: '10.10.3.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

// Private DNS Zone for PostgreSQL Flexible Server
resource pgPrivateDns 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: '${prefix}.postgres.database.azure.com'
  location: 'global'
  tags: tags
}

resource pgPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: pgPrivateDns
  name: '${prefix}-pg-dns-link'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}

// Private DNS Zone for Key Vault
resource kvPrivateDns 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
  tags: tags
}

resource kvPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: kvPrivateDns
  name: '${prefix}-kv-dns-link'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}

// Private DNS Zone for Storage
resource storagePrivateDns 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.dfs.core.windows.net'
  location: 'global'
  tags: tags
}

resource storagePrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: storagePrivateDns
  name: '${prefix}-stg-dns-link'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}

// Private DNS Zone for Cosmos DB (NoSQL / documents)
resource cosmosPrivateDns 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.documents.azure.com'
  location: 'global'
  tags: tags
}

resource cosmosPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: cosmosPrivateDns
  name: '${prefix}-cosmos-dns-link'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}

// Private DNS Zone for Azure OpenAI (Cognitive Services)
resource aoaiPrivateDns 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.openai.azure.com'
  location: 'global'
  tags: tags
}

resource aoaiPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: aoaiPrivateDns
  name: '${prefix}-aoai-dns-link'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}

// Azure Policy: Deny public network access on scoped resource types
resource denyPublicNetworkPolicy 'Microsoft.Authorization/policyAssignments@2022-06-01' = {
  name: '${prefix}-deny-public-network'
  location: location
  properties: {
    displayName: 'MyDude — Deny public network access'
    description: 'Enforces private-only access for PostgreSQL, Key Vault, and Storage.'
    policyDefinitionId: '/providers/Microsoft.Authorization/policyDefinitions/b52376f7-9612-48a1-81cd-1ffe4b61032c'
    enforcementMode: 'Default'
  }
}

output vnetId string = vnet.id
output acaSubnetId string = '${vnet.id}/subnets/${prefix}-aca-subnet'
output pgSubnetId string = '${vnet.id}/subnets/${prefix}-pg-subnet'
output peSubnetId string = '${vnet.id}/subnets/${prefix}-pe-subnet'
output pgPrivateDnsZoneId string = pgPrivateDns.id
output kvPrivateDnsZoneId string = kvPrivateDns.id
output storagePrivateDnsZoneId string = storagePrivateDns.id
output cosmosPrivateDnsZoneId string = cosmosPrivateDns.id
output aoaiPrivateDnsZoneId string = aoaiPrivateDns.id
