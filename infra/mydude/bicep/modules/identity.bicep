// MyDude — Managed Identities
// Single authority rule: bcsGateIdentity is the ONLY Unity Catalog writer.
// All other identities are catalog read-only or scoped to specific Postgres DBs.

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object

resource bcsGateIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-bcs-gate'
  location: location
  tags: tags
}

resource foundryAgentIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-foundry-agent'
  location: location
  tags: tags
}

resource agentsHomeIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-agents-home-db'
  location: location
  tags: tags
}

resource providerHomeIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-provider-home-db'
  location: location
  tags: tags
}

resource readonlyIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-readonly'
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------
// Outputs — only IDs and principalIds (never secrets)
// ---------------------------------------------------------------------------
output bcsGateIdentityId string = bcsGateIdentity.id
output bcsGatePrincipalId string = bcsGateIdentity.properties.principalId

output foundryAgentIdentityId string = foundryAgentIdentity.id
output foundryAgentPrincipalId string = foundryAgentIdentity.properties.principalId

output agentsHomeIdentityId string = agentsHomeIdentity.id
output agentsHomePrincipalId string = agentsHomeIdentity.properties.principalId

output providerHomeIdentityId string = providerHomeIdentity.id
output providerHomePrincipalId string = providerHomeIdentity.properties.principalId

output readonlyIdentityId string = readonlyIdentity.id
output readonlyPrincipalId string = readonlyIdentity.properties.principalId
