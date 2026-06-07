// =============================================================================
// MyDude Total Stack — Main Bicep entrypoint
// Resource Group: MyDude
// Deploy: az deployment group create --resource-group MyDude --template-file main.bicep --parameters @parameters.json
// =============================================================================

targetScope = 'resourceGroup'

@description('Azure region for all resources')
param location string = 'eastus2'

@description('Environment tag (prod | staging | dev)')
param environment string = 'prod'

@description('PostgreSQL admin login (used only for initial setup; app uses managed identity)')
@secure()
param pgAdminPassword string

@description('Azure AD tenant ID')
param tenantId string = subscription().tenantId

@description(
  'Databricks SQL Warehouse ID for BCS gate DDL bootstrap and claim INSERT. '
  'Provisioned post-deploy via Databricks REST API (not an Azure RM resource). '
  'See PROVISIONING.md §4b. Leave empty on first deploy; update before running unity_migrator.py.'
)
param databricksSqlWarehouseId string = ''

var prefix = 'mydude'
var tags = {
  project: 'MyDude'
  environment: environment
  managedBy: 'bicep'
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

module containerApps 'modules/container_apps.bicep' = {
  name: 'mydude-container-apps'
  params: {
    location: location
    prefix: prefix
    tags: tags
    acaSubnetId: network.outputs.acaSubnetId
    bcsGateIdentityId: identity.outputs.bcsGateIdentityId
    readonlyIdentityId: identity.outputs.readonlyIdentityId
    keyVaultName: keyvault.outputs.keyVaultName
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    unityCatalogEndpoint: unityCatalog.outputs.unityCatalogEndpoint
    // SQL Warehouse ID — provisioned post-deploy via Databricks REST API (not an Azure RM resource;
    // Bicep cannot create Databricks SQL Warehouses). See PROVISIONING.md §4b.
    // Pass via: az deployment group create ... --parameters databricksSqlWarehouseId=<id>
    databricksSqlWarehouseId: databricksSqlWarehouseId
  }
  dependsOn: [keyvault, monitoring, unityCatalog]
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
  }
  dependsOn: [monitoring, storage, network]
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'mydude-monitoring'
  params: {
    location: location
    prefix: prefix
    tags: tags
  }
}

module unityCatalog 'modules/unity_catalog.bicep' = {
  name: 'mydude-unity-catalog'
  params: {
    location: location
    prefix: prefix
    tags: tags
    bcsGatePrincipalId: identity.outputs.bcsGatePrincipalId
    readonlyPrincipalId: identity.outputs.readonlyPrincipalId
    peSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    storageAccountId: storage.outputs.adlsId
    dbrPublicSubnetName: network.outputs.dbrPublicSubnetName
    dbrPrivateSubnetName: network.outputs.dbrPrivateSubnetName
    dbrPrivateDnsZoneId: network.outputs.dbrPrivateDnsZoneId
  }
  dependsOn: [identity, network, storage]
}

module aiSearch 'modules/ai_search.bicep' = {
  name: 'mydude-ai-search'
  params: {
    location: location
    prefix: prefix
    tags: tags
    readonlyPrincipalId: identity.outputs.readonlyPrincipalId
  }
}

// ---------------------------------------------------------------------------
// Outputs (non-secret; reference Key Vault for secrets)
// ---------------------------------------------------------------------------

output resourceGroupName string = resourceGroup().name
output bcsGateUrl string = containerApps.outputs.bcsGateInternalUrl
output fanoutGatewayUrl string = containerApps.outputs.fanoutGatewayExternalUrl
output postgresServerFqdn string = postgres.outputs.serverFqdn
output keyVaultUri string = keyvault.outputs.keyVaultUri
output adlsAccountName string = storage.outputs.adlsAccountName
output foundryEndpoint string = foundry.outputs.foundryEndpoint
output unityCatalogEndpoint string = unityCatalog.outputs.unityCatalogEndpoint
output databricksWorkspaceUrl string = unityCatalog.outputs.databricksWorkspaceUrl
