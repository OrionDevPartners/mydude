// =============================================================================
// MyDude Total Stack — Main Bicep entrypoint
// Resource Group: mydude (existing; SP is Owner on this RG only)
// Deploy: az deployment group create --resource-group mydude --template-file main.bicep --parameters @parameters.json
//
// Topology (Azure-native, private):
//   - Postgres Flexible Server (D4ds_v5, ZoneRedundant HA) — relational / governance / audit / secrets authority
//   - Cosmos DB (NoSQL, vector) — agent swarm memory (document + vector search)
//   - Microsoft Fabric capacity — domain-knowledge corpus lakehouse (OneLake)
//   - Azure OpenAI (private) — foreground + background (agent-mesh) gpt-4.1-mini deployments
//   - Key Vault, ADLS Gen2 storage, VNet + private endpoints + DNS, monitoring
//
// Vector search lives in Cosmos DB + Postgres pgvector; the knowledge corpus
// lives in Fabric/OneLake. No separate app-code container apps.
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

@description('Deploy the Azure MCP Dev Accelerator container app. Default false. Set true once the MCP container image has been built and pushed to a registry the foundry-agent identity can pull from.')
param deployAzureMcp bool = false

@description('Fully-qualified MCP container image (REQUIRED when deployAzureMcp=true), e.g. myregistry.azurecr.io/mydude-azure-mcp:2026-06-14.')
param azureMcpImage string = ''

@description('Container registry login server for the MCP image pull (REQUIRED when deployAzureMcp=true), e.g. myregistry.azurecr.io.')
param azureMcpRegistryServer string = ''

@description('Enable the BILLABLE two-phase deploy APPLY tool inside the MCP server. Default false (default-deny).')
param azureMcpEnableDeploy bool = false

@description('Host allow-list (comma-separated) pinning the MCP server to its own address (DNS-rebinding hardening). Leave EMPTY on the first deploy (the app FQDN is not yet known; internal ingress + bearer auth still guard it). On the SECOND deploy, set this to the app FQDN from the azureMcpUrl output to drop the host-check opt-out and pin the server. PUBLIC posture (azureMcpExternalIngress=true): REQUIRED from the FIRST public deploy (phase 1) — set this to the custom domain even before it is bound; the host-check opt-out is never taken in public mode and the deploy preflight fails loud if it is empty.')
param azureMcpAllowedHosts string = ''

@description('MCP posture. false (default, GOVERNANCE DEFAULT): VNet-internal managed environment + internal ingress. true: external managed environment + PUBLIC ingress (internet-reachable) — the bearer token becomes the sole gate. The environment\'s internal flag is immutable post-create; flipping posture on an existing env requires deleting + recreating the (stateless) MCP env+app.')
param azureMcpExternalIngress bool = false

@description('Public custom domain for the MCP server (e.g. MydudeMCP.com). Requires azureMcpExternalIngress=true. Two-phase: deploy with this EMPTY first to obtain azureMcpStaticIp + azureMcpCustomDomainVerificationId, create the DNS records (apex A-record -> static IP, TXT asuid.<domain> -> verification id), then deploy again with this set to mint + bind the managed TLS certificate.')
param azureMcpCustomDomain string = ''

@description('Domain-control validation method for the MCP managed certificate. TXT (default) suits an APEX domain; use CNAME for a subdomain or HTTP for token-over-HTTP validation.')
@allowed([
  'TXT'
  'CNAME'
  'HTTP'
])
param azureMcpDomainValidation string = 'TXT'

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
    keyVaultId: keyvault.outputs.keyVaultId
    appInsightsId: monitoring.outputs.appInsightsId
    amlApiPrivateDnsZoneId: network.outputs.amlApiPrivateDnsZoneId
    amlNotebooksPrivateDnsZoneId: network.outputs.amlNotebooksPrivateDnsZoneId
    blobPrivateDnsZoneId: network.outputs.blobPrivateDnsZoneId
    filePrivateDnsZoneId: network.outputs.filePrivateDnsZoneId
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

// Azure MCP Dev Accelerator — VNet-internal Container App (off by default).
// Requires the Microsoft.App + Microsoft.OperationalInsights resource providers
// to be registered (a subscription-admin step). The container image must already
// be pushed to a registry the foundry-agent identity can pull from.
module mcp 'modules/mcp.bicep' = if (deployAzureMcp) {
  name: 'mydude-azure-mcp'
  params: {
    location: location
    prefix: prefix
    tags: tags
    acaSubnetId: network.outputs.acaSubnetId
    logAnalyticsWorkspaceName: monitoring.outputs.logAnalyticsWorkspaceName
    userAssignedIdentityId: identity.outputs.foundryAgentIdentityId
    containerImage: azureMcpImage
    containerRegistryServer: azureMcpRegistryServer
    subscriptionId: subscription().subscriptionId
    enableAzureDeploy: azureMcpEnableDeploy
    allowedHosts: azureMcpAllowedHosts
    externalIngress: azureMcpExternalIngress
    customDomain: azureMcpCustomDomain
    domainControlValidation: azureMcpDomainValidation
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
output azureMcpAppName string = deployAzureMcp ? mcp.outputs.containerAppName : 'NOT_DEPLOYED (deployAzureMcp=false)'
// The endpoint URL: the bound custom-domain URL when set, else the default FQDN URL.
output azureMcpUrl string = deployAzureMcp ? (empty(azureMcpCustomDomain) ? mcp.outputs.containerAppUrl : mcp.outputs.customDomainUrl) : 'NOT_DEPLOYED (deployAzureMcp=false)'
// Public custom-domain DNS setup (phase 1 -> create records -> phase 2). For the
// apex domain: A-record <domain> -> azureMcpStaticIp; TXT asuid.<domain> -> azureMcpCustomDomainVerificationId.
output azureMcpStaticIp string = deployAzureMcp ? mcp.outputs.managedEnvStaticIp : 'NOT_DEPLOYED (deployAzureMcp=false)'
output azureMcpCustomDomainVerificationId string = deployAzureMcp ? mcp.outputs.customDomainVerificationId : 'NOT_DEPLOYED (deployAzureMcp=false)'
