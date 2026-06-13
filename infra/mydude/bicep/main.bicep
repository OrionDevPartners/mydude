// =============================================================================
// MyDude Total Stack — Main Bicep entrypoint
// Resource Group: mydude (existing; SP is Owner on this RG only)
// Deploy: az deployment group create --resource-group mydude --template-file main.bicep --parameters @parameters.json
//
// Topology (Azure-native, private):
//   - Postgres Flexible Server (D4ds_v5, ZoneRedundant HA) — relational / governance / audit / secrets authority
//   - Cosmos DB (NoSQL, vector) — agent swarm memory (document + vector); replaces Azure AI Search
//   - Microsoft Fabric capacity — domain-knowledge corpus lakehouse (OneLake)
//   - Azure OpenAI (private) — foreground + background (agent-mesh) gpt-4.1-mini deployments
//   - Key Vault, ADLS Gen2 storage, VNet + private endpoints + DNS, monitoring
//
// Dropped from this stack: Databricks/Unity Catalog, app-code container apps, Azure AI Search.
// =============================================================================

targetScope = 'resourceGroup'

@description('Azure region for all resources')
param location string = 'eastus2'

@description('Environment tag (prod | staging | dev)')
param environment string = 'prod'

@description('PostgreSQL admin login password (initial setup; app uses managed identity)')
@secure()
param pgAdminPassword string

@description('Azure AD tenant ID')
param tenantId string = subscription().tenantId

@description('Fabric capacity SKU (F2, F4, F8, F16, F32, F64, ...). Billed per-hour while running.')
param fabricSkuName string = 'F32'

@description('Fabric capacity admin members — UPNs/emails or AAD object IDs. At least one REQUIRED.')
param fabricAdminMembers array

@description('Deploy the Microsoft Fabric capacity. Default true. Set false when the deploy service principal lacks tenant authorization to create a Fabric capacity (capacity creation needs AAD/Graph authorization an RG-scoped SP cannot satisfy — fails with "Unable to authorize with Azure Active Directory"). Create the capacity as a separate admin step, then flip this back to true.')
param fabricEnabled bool = true

@description('Deploy the AI Foundry Hub + Project (managed agent runtime). Default true. Set false to ship the AOAI account/deployments without the Hub until the Hub workspace dependencies (dedicated non-HNS storage, KeyVault, App Insights, AML private endpoint/DNS) are added.')
param foundryHubEnabled bool = true

@description('AOAI foreground (interactive) gpt-4.1-mini deployment capacity (x1000 TPM).')
param aoaiForegroundCapacity int = 250

@description('AOAI background (agent-mesh) gpt-4.1-mini deployment capacity (x1000 TPM).')
param aoaiBackgroundCapacity int = 100

@description('Cosmos DB autoscale MAX RU/s for the agents-memory database.')
param cosmosMaxThroughput int = 10000

var prefix = 'mydude'
var tags = {
  project: 'MyDude'
  environment: environment
  managedBy: 'bicep'
  tenantId: tenantId
}

// ---------------------------------------------------------------------------
// Modules
// ---------------------------------------------------------------------------

module identity 'modules/identity.bicep' = {
  name: 'mydude-identity'
  params: {
    location: location
    prefix: prefix
    tags: tags
  }
}

module network 'modules/network.bicep' = {
  name: 'mydude-network'
  params: {
    location: location
    prefix: prefix
    tags: tags
  }
}

module keyvault 'modules/keyvault.bicep' = {
  name: 'mydude-keyvault'
  params: {
    location: location
    prefix: prefix
    tags: tags
    bcsGatePrincipalId: identity.outputs.bcsGatePrincipalId
    foundryAgentPrincipalId: identity.outputs.foundryAgentPrincipalId
    agentsHomePrincipalId: identity.outputs.agentsHomePrincipalId
    providerHomePrincipalId: identity.outputs.providerHomePrincipalId
    peSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    kvPrivateDnsZoneId: network.outputs.kvPrivateDnsZoneId
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'mydude-postgres'
  params: {
    location: location
    prefix: prefix
    tags: tags
    adminPassword: pgAdminPassword
    delegatedSubnetId: network.outputs.pgSubnetId
    privateDnsZoneId: network.outputs.pgPrivateDnsZoneId
  }
}

module storage 'modules/storage.bicep' = {
  name: 'mydude-storage'
  params: {
    location: location
    prefix: prefix
    tags: tags
    bcsGatePrincipalId: identity.outputs.bcsGatePrincipalId
    readonlyPrincipalId: identity.outputs.readonlyPrincipalId
    peSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    storageDnsZoneId: network.outputs.storagePrivateDnsZoneId
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'mydude-monitoring'
  params: {
    location: location
    prefix: prefix
    tags: tags
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'mydude-foundry'
  params: {
    location: location
    prefix: prefix
    tags: tags
    foundryAgentIdentityId: identity.outputs.foundryAgentIdentityId
    foundryAgentPrincipalId: identity.outputs.foundryAgentPrincipalId
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    storageAccountId: storage.outputs.adlsId
    acaSubnetId: network.outputs.acaSubnetId
    peSubnetId: network.outputs.peSubnetId
    aoaiPrivateDnsZoneId: network.outputs.aoaiPrivateDnsZoneId
    aoaiForegroundCapacity: aoaiForegroundCapacity
    aoaiBackgroundCapacity: aoaiBackgroundCapacity
    foundryHubEnabled: foundryHubEnabled
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'mydude-cosmos'
  params: {
    location: location
    prefix: prefix
    tags: tags
    peSubnetId: network.outputs.peSubnetId
    cosmosPrivateDnsZoneId: network.outputs.cosmosPrivateDnsZoneId
    agentDataContributorPrincipalId: identity.outputs.agentsHomePrincipalId
    readonlyPrincipalId: identity.outputs.readonlyPrincipalId
    cosmosMaxThroughput: cosmosMaxThroughput
  }
}

module fabric 'modules/fabric.bicep' = if (fabricEnabled) {
  name: 'mydude-fabric'
  params: {
    location: location
    prefix: prefix
    tags: tags
    fabricSkuName: fabricSkuName
    fabricAdminMembers: fabricAdminMembers
  }
}

// ---------------------------------------------------------------------------
// Outputs (non-secret; reference Key Vault for secrets)
// ---------------------------------------------------------------------------

output resourceGroupName string = resourceGroup().name
output postgresServerFqdn string = postgres.outputs.serverFqdn
output keyVaultUri string = keyvault.outputs.keyVaultUri
output adlsAccountName string = storage.outputs.adlsAccountName
output foundryEndpoint string = foundry.outputs.foundryEndpoint
output aoaiEndpoint string = foundry.outputs.aoaiEndpoint
output cosmosAccountName string = cosmos.outputs.cosmosAccountName
output cosmosEndpoint string = cosmos.outputs.cosmosEndpoint
output fabricCapacityName string = fabricEnabled ? fabric.outputs.fabricCapacityName : 'NOT_DEPLOYED (fabricEnabled=false; create capacity as an admin step)'
