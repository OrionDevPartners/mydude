// MyDude — Foundry Agent Service (full depth: managed runtime, gateway, browser, voice)
//
// AUTHORITY RULE: The Foundry Agent Service identity is scoped to tool/runtime ONLY.
// It NEVER holds Unity Catalog write (Storage Blob Data Contributor is withheld).
//
// Model-router confinement is enforced by:
//   1. RBAC: foundryAgentIdentity gets only "Cognitive Services OpenAI User" on AOAI —
//      inference-only; cannot list, create, or delete deployments.
//   2. Data-plane: model router reads ONLY from agents_home.policy.model_team_policy
//      before dispatching — never the raw AOAI deployment list.
//   3. Network: AOAI account is private-endpoint only; Foundry accesses via VNet.
//
// Capability deployment gates:
//   browser — conditional on foundryBrowserEnabled=true (default: false)
//              Provisions Azure Playwright Service workspace when enabled.
//   voice   — conditional on foundryVoiceEnabled=true (default: false)
//              Provisions Azure Communication Services + Speech when enabled.
//   code_exec — always enabled (sandboxed ACI, not general-purpose compute)
//   file_ops  — read-only, scoped to mlflow-artifacts container only

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object
param foundryAgentIdentityId string
param foundryAgentPrincipalId string
param logAnalyticsWorkspaceId string
param storageAccountId string          // Foundry gets NO write role on this account
param acaSubnetId string               // VNet subnet Foundry agents run in
param peSubnetId string                // Private endpoint subnet for AOAI private link
param aoaiPrivateDnsZoneId string      // privatelink.openai.azure.com zone ID from network.bicep

@description('Enable browser capability (Azure Playwright Service). Default false.')
param foundryBrowserEnabled bool = false

@description('Enable voice capability (Azure Communication Services + Speech). Default false.')
param foundryVoiceEnabled bool = false

@description('Deploy the AI Foundry Hub + Project + AOAI connection (managed agent runtime). Default true. The Hub workspace requires a dedicated NON-HNS workspace storage account + KeyVault + App Insights + an AML private endpoint/DNS; until that surface is added, set false to ship the AOAI account/deployments (which the app uses directly over the AOAI private endpoint) without the managed runtime.')
param foundryHubEnabled bool = true

@description('AOAI foreground (interactive) gpt-4.1-mini capacity (x1000 TPM).')
param aoaiForegroundCapacity int = 250

@description('AOAI background (agent-mesh) gpt-4.1-mini capacity (x1000 TPM).')
param aoaiBackgroundCapacity int = 100

// ---------------------------------------------------------------------------
// AOAI Account (private; holds the MyDude-granted model deployments)
// ---------------------------------------------------------------------------
resource aoaiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${prefix}-aoai'
  location: location
  tags: union(tags, { scope: 'foundry-runtime', catalog_write: 'false' })
  kind: 'OpenAI'
  sku: { name: 'S0' }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${foundryAgentIdentityId}': {} }
  }
  properties: {
    // 'Disabled' blocks all traffic except private endpoints.
    // VNet service endpoint rules are NOT honored when publicNetworkAccess='Disabled';
    // private endpoint + DNS zone group is the correct access pattern.
    publicNetworkAccess: 'Disabled'
    customSubDomainName: '${prefix}-aoai'
    networkAcls: {
      defaultAction: 'Deny'
      virtualNetworkRules: []  // empty — private endpoint below is the sole access path
      ipRules: []
    }
  }
}

// ---------------------------------------------------------------------------
// AOAI private endpoint — sole access path from VNet (ACA subnet → AOAI)
// ---------------------------------------------------------------------------
resource aoaiPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${prefix}-aoai-pe'
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [
      {
        name: '${prefix}-aoai-plsc'
        properties: {
          privateLinkServiceId: aoaiAccount.id
          groupIds: ['account']
        }
      }
    ]
  }
}

// DNS zone group — routes <prefix>-aoai.openai.azure.com to the private IP.
// Required so Foundry (running in ACA) can resolve the AOAI endpoint by name.
resource aoaiDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: aoaiPrivateEndpoint
  name: 'aoaiDnsGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-openai'
        properties: {
          privateDnsZoneId: aoaiPrivateDnsZoneId
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// RBAC: foundryAgentIdentity → "Cognitive Services OpenAI User" (inference only)
// Role ID: 5e0bd9bd-7b93-4f28-af87-19fc36ad61bd
// Cannot manage deployments, rotate keys, or read billing data.
// ---------------------------------------------------------------------------
resource foundryAoaiUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aoaiAccount.id, foundryAgentPrincipalId, '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  scope: aoaiAccount
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
    )
    principalId: foundryAgentPrincipalId
    principalType: 'ServicePrincipal'
    description: 'Foundry Agent: inference-only on MyDude AOAI. No deployment management.'
  }
}

// ---------------------------------------------------------------------------
// Model deployments — ONLY those in the MyDude-granted model set
// ---------------------------------------------------------------------------
resource gpt41MiniDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aoaiAccount
  name: 'gpt-41-mini'
  tags: union(tags, { granted_by: 'agents_home.policy.model_team_policy', exec_locus: 'in_azure', tier: 'foreground' })
  sku: { name: 'GlobalStandard', capacity: aoaiForegroundCapacity }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1-mini'
      version: '2025-04-14'
    }
    versionUpgradeOption: 'NoAutoUpgrade'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  // Account-adjacent ops (PE/DNS/role) re-PUT on Incremental deploys flip the
  // AOAI account into a transient 'Accepted' state; serialize deployment creates
  // behind them so they never race the account ("AccountProvisioningStateInvalid").
  dependsOn: [aoaiPrivateEndpoint, aoaiDnsGroup, foundryAoaiUserRoleAssignment]
}

// Background (agent-mesh) deployment — same model, separate capacity so the 24/7
// low-throttle mesh runs isolated from interactive traffic. AOAI serializes deployment
// operations on an account, so this dependsOn the foreground deployment. Background
// priority is enforced in app routing (AOAI has no native low-priority SKU).
resource gpt41MiniBgDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aoaiAccount
  name: 'gpt-41-mini-bg'
  tags: union(tags, { granted_by: 'agents_home.policy.model_team_policy', exec_locus: 'in_azure', tier: 'background' })
  sku: { name: 'GlobalStandard', capacity: aoaiBackgroundCapacity }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1-mini'
      version: '2025-04-14'
    }
    versionUpgradeOption: 'NoAutoUpgrade'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [gpt41MiniDeployment]
}

// ---------------------------------------------------------------------------
// Azure AI Foundry Hub — workspace that hosts the Agent managed runtime
// ---------------------------------------------------------------------------
resource foundryHub 'Microsoft.MachineLearningServices/workspaces@2024-04-01' = if (foundryHubEnabled) {
  name: '${prefix}-foundry'
  location: location
  tags: union(tags, { role: 'foundry-hub', catalog_write: 'false', scope: 'tool-runtime-only' })
  kind: 'Hub'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${foundryAgentIdentityId}': {} }
  }
  properties: {
    friendlyName: 'MyDude Foundry Hub'
    description: 'MyDude AI Foundry Hub — tool/runtime scope only. No catalog write.'
    publicNetworkAccess: 'Disabled'
    workspaceHubConfig: {
      // Must be the FULL resource-group ARM ID (/subscriptions/.../resourceGroups/x),
      // not just the name, or the RP throws "Error parsing DefaultWorkspaceResourceGroup".
      defaultWorkspaceResourceGroup: resourceGroup().id
    }
  }
}

// ---------------------------------------------------------------------------
// Foundry Project — per-application agent project within the hub
// ---------------------------------------------------------------------------
resource foundryProject 'Microsoft.MachineLearningServices/workspaces@2024-04-01' = if (foundryHubEnabled) {
  name: '${prefix}-foundry-project'
  location: location
  tags: union(tags, { role: 'foundry-project', catalog_write: 'false', scope: 'tool-runtime-only' })
  kind: 'Project'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${foundryAgentIdentityId}': {} }
  }
  properties: {
    friendlyName: 'MyDude Agent Project'
    hubResourceId: foundryHub.id
    publicNetworkAccess: 'Disabled'
  }
}

// ---------------------------------------------------------------------------
// Foundry Agent Service connection to AOAI
// metadata.model_router_confinement signals to the app layer which table
// to query before dispatching — the router never reads the raw deployment list.
// ---------------------------------------------------------------------------
resource foundryAoaiConnection 'Microsoft.MachineLearningServices/workspaces/connections@2024-04-01' = if (foundryHubEnabled) {
  parent: foundryProject
  name: '${prefix}-aoai-connection'
  properties: {
    category: 'AzureOpenAI'
    target: aoaiAccount.properties.endpoint
    authType: 'ManagedIdentity'
    isSharedToAll: false
    metadata: {
      ApiVersion: '2024-10-01-preview'
      ApiType: 'azure'
      ResourceId: aoaiAccount.id
      model_router_confinement: 'agents_home.policy.model_team_policy'
    }
  }
  // AOAI accounts return from ARM create while still provisioning ("Accepted").
  // Creating the connection too early fails with AccountProvisioningStateInvalid.
  // Gate the connection on the model deployments, which only complete once the
  // account is fully Succeeded.
  dependsOn: [gpt41MiniDeployment, gpt41MiniBgDeployment]
}

// ---------------------------------------------------------------------------
// Browser capability — Azure Playwright Service
// Deployed only when foundryBrowserEnabled=true.
// To enable: pass --parameters foundryBrowserEnabled=true at deployment time.
// ---------------------------------------------------------------------------
resource playwrightWorkspace 'Microsoft.Playwright/workspaces@2024-08-01-preview' = if (foundryBrowserEnabled) {
  name: '${prefix}-playwright'
  location: location
  tags: union(tags, { role: 'foundry-browser', capability_gate: 'foundryBrowserEnabled' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${foundryAgentIdentityId}': {} }
  }
  properties: {
    regionalAffinity: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Voice capability — Azure Communication Services + Speech Service
// Deployed only when foundryVoiceEnabled=true.
// To enable: pass --parameters foundryVoiceEnabled=true at deployment time.
// ---------------------------------------------------------------------------
resource communicationService 'Microsoft.Communication/communicationServices@2023-04-01' = if (foundryVoiceEnabled) {
  name: '${prefix}-acs'
  location: 'global'
  tags: union(tags, { role: 'foundry-voice', capability_gate: 'foundryVoiceEnabled' })
  properties: {
    dataLocation: 'United States'
  }
}

resource speechService 'Microsoft.CognitiveServices/accounts@2024-10-01' = if (foundryVoiceEnabled) {
  name: '${prefix}-speech'
  location: location
  tags: union(tags, { role: 'foundry-voice', capability_gate: 'foundryVoiceEnabled' })
  kind: 'SpeechServices'
  sku: { name: 'S0' }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${foundryAgentIdentityId}': {} }
  }
  properties: {
    publicNetworkAccess: 'Disabled'
    customSubDomainName: '${prefix}-speech'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
output foundryHubId string = foundryHubEnabled ? foundryHub.id : 'NOT_DEPLOYED (foundryHubEnabled=false)'
output foundryProjectId string = foundryHubEnabled ? foundryProject.id : 'NOT_DEPLOYED (foundryHubEnabled=false)'
output foundryEndpoint string = foundryHubEnabled ? 'https://${prefix}-foundry.services.ai.azure.com' : 'NOT_DEPLOYED (foundryHubEnabled=false; AOAI delivered directly via its private endpoint)'
output aoaiEndpoint string = aoaiAccount.properties.endpoint
output aoaiAccountId string = aoaiAccount.id
output foregroundDeploymentName string = gpt41MiniDeployment.name
output bgDeploymentName string = gpt41MiniBgDeployment.name
output browserEnabled bool = foundryBrowserEnabled
output voiceEnabled bool = foundryVoiceEnabled
output modelRouterConfinementNote string = 'Foundry model router reads agents_home.policy.model_team_policy; AOAI deployment list is never the authority.'
