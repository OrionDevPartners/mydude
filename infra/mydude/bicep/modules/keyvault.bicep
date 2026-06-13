// MyDude — Key Vault with private endpoint and access policies

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object
param bcsGatePrincipalId string
param foundryAgentPrincipalId string
param agentsHomePrincipalId string
param providerHomePrincipalId string
param peSubnetId string
param vnetId string
param kvPrivateDnsZoneId string

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv'
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    enableRbacAuthorization: false
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      virtualNetworkRules: []
      ipRules: []
    }
    accessPolicies: [
      // BCS Gate — full secret access (truth writer needs its idempotency key)
      {
        tenantId: subscription().tenantId
        objectId: bcsGatePrincipalId
        permissions: {
          secrets: ['get', 'set', 'list']
        }
      }
      // Foundry Agent — read/list, plus set so the AI Foundry Hub (which runs
      // under this same user-assigned identity) can persist its connection
      // secrets into the vault. This KV is the Hub's wired Key Vault backing.
      {
        tenantId: subscription().tenantId
        objectId: foundryAgentPrincipalId
        permissions: {
          secrets: ['get', 'list', 'set']
        }
      }
      // agents_home DB identity — get only (connection string)
      {
        tenantId: subscription().tenantId
        objectId: agentsHomePrincipalId
        permissions: {
          secrets: ['get']
        }
      }
      // provider_home DB identity — get only (connection string)
      {
        tenantId: subscription().tenantId
        objectId: providerHomePrincipalId
        permissions: {
          secrets: ['get']
        }
      }
    ]
  }
}

// Private endpoint for Key Vault
resource kvPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${prefix}-kv-pe'
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [
      {
        name: '${prefix}-kv-plsc'
        properties: {
          privateLinkServiceId: keyVault.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

// DNS zone group — links the KV private endpoint to the private DNS zone so
// VNet name resolution routes <vault>.vault.azure.net to the private IP.
// Without this, Container Apps cannot resolve the vault FQDN over private link.
resource kvPrivateEndpointDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: kvPrivateEndpoint
  name: 'kvDnsGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-vaultcore'
        properties: {
          privateDnsZoneId: kvPrivateDnsZoneId
        }
      }
    ]
  }
}

output keyVaultName string = keyVault.name
output keyVaultId string = keyVault.id
output keyVaultUri string = keyVault.properties.vaultUri
