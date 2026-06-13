// MyDude — Foundry Agent Service (full depth: managed runtime, gateway, browser, voice)
//
// AUTHORITY RULE: The Foundry Agent Service identity is scoped to tool/runtime ONLY.
// It NEVER holds governance-ledger or knowledge-corpus write (Storage Blob Data Contributor is withheld). No catalog write.
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
param keyVaultId string                 // existing mydude-kv — wired as the Hub's Key Vault backing
param appInsightsId string              // existing mydude-appinsights — wired as the Hub's App Insights backing
param amlApiPrivateDnsZoneId string     // privatelink.api.azureml.ms zone ID from network.bicep
param amlNotebooksPrivateDnsZoneId string // privatelink.notebooks.azure.net zone ID from network.bicep
param blobPrivateDnsZoneId string       // privatelink.blob.core.windows.net zone ID (Hub workspace storage)
param filePrivateDnsZoneId string       // privatelink.file.core.windows.net zone ID (Hub workspace storage)

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
// Dedicated Hub workspace storage — GPv2, NON-HNS (the shared mydudestg is
// HNS-enabled ADLS, which AML rejects as primary workspace storage). Private:
// public access disabled, reached only via blob + file private endpoints.
// Gated on foundryHubEnabled — only provisioned when the Hub is enabled.
// ---------------------------------------------------------------------------
resource foundryStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (foundryHubEnabled) {
  name: '${prefix}foundrystg'
  location: location
  tags: union(tags, { role: 'foundry-hub-storage', hns: 'false' })
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    isHnsEnabled: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
      virtualNetworkRules: []
      ipRules: []
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

// Blob private endpoint for the Hub workspace storage.
resource foundryStorageBlobPe 'Microsoft.Network/privateEndpoints@2023-11-01' = if (foundryHubEnabled) {
  name: '${prefix}-foundrystg-blob-pe'
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [
      {
        name: '${prefix}-foundrystg-blob-plsc'
        properties: {
          privateLinkServiceId: foundryStorage.id
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource foundryStorageBlobDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (foundryHubEnabled) {
  parent: foundryStorageBlobPe
  name: 'blobDnsGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-blob'
        properties: { privateDnsZoneId: blobPrivateDnsZoneId }
      }
    ]
  }
}

// File private endpoint for the Hub workspace storage (AML requires file access).
resource foundryStorageFilePe 'Microsoft.Network/privateEndpoints@2023-11-01' = if (foundryHubEnabled) {
  name: '${prefix}-foundrystg-file-pe'
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [
      {
        name: '${prefix}-foundrystg-file-plsc'
        properties: {
          privateLinkServiceId: foundryStorage.id
          groupIds: ['file']
        }
      }
    ]
  }
}

resource foundryStorageFileDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (foundryHubEnabled) {
  parent: foundryStorageFilePe
  name: 'fileDnsGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-file'
        properties: { privateDnsZoneId: filePrivateDnsZoneId }
      }
    ]
  }
}

// RBAC: the Hub's user-assigned identity needs data-plane access to its OWN
// workspace storage. AML does not auto-grant this for user-assigned-identity
// workspaces, so the workspace fails to operate without these roles.
// Storage Blob Data Contributor: ba92f5b4-2d11-453d-a403-e96b0029c9fe
resource foundryStorageBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (foundryHubEnabled) {
  name: guid(foundryStorage.id, foundryAgentPrincipalId, 'StorageBlobDataContributor')
  scope: foundryStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: foundryAgentPrincipalId
    principalType: 'ServicePrincipal'
    description: 'Foundry Hub identity: blob data on its dedicated workspace storage.'
  }
}

// Storage File Data Privileged Contributor: 69566ab7-960f-475b-8e7c-b3118f30c6bd
resource foundryStorageFileRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (foundryHubEnabled) {
  name: guid(foundryStorage.id, foundryAgentPrincipalId, 'StorageFileDataPrivilegedContributor')
  scope: foundryStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69566ab7-960f-475b-8e7c-b3118f30c6bd')
    principalId: foundryAgentPrincipalId
    principalType: 'ServicePrincipal'
    description: 'Foundry Hub identity: file data on its dedicated workspace storage.'
  }
}

// ---------------------------------------------------------------------------
// Azure AI Foundry Hub — workspace that hosts the Agent managed runtime.
// Backed by: dedicated NON-HNS storage (above), the shared Key Vault and
// App Insights (wired by ID), and managed-network isolation. Reached privately
// via the amlworkspace private endpoint below.
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
    // A Hub workspace fails with an opaque "InternalServerError: Received 400"
    // when these backing resources are absent. NON-HNS storage is mandatory.
    storageAccount: foundryStorage.id
    keyVault: keyVaultId
    applicationInsights: appInsightsId
    // Managed VNet isolation: AML provisions a managed network for the Hub's
    // compute; inbound is private-only (the amlworkspace PE below), outbound is
    // managed. AllowInternetOutbound keeps managed outbound working without
    // hand-maintaining approved-FQDN rules; harden to AllowOnlyApprovedOutbound
    // once the required outbound rule set is enumerated.
    managedNetwork: {
      isolationMode: 'AllowInternetOutbound'
    }
    workspaceHubConfig: {
      // Must be the FULL resource-group ARM ID (/subscriptions/.../resourceGroups/x),
      // not just the name, or the RP throws "Error parsing DefaultWorkspaceResourceGroup".
      defaultWorkspaceResourceGroup: resourceGroup().id
    }
  }
  // Storage data-plane roles must exist before the workspace initializes against
  // its primary storage, or first-run operations are denied.
  dependsOn: [foundryStorageBlobRole, foundryStorageFileRole]
}

// ---------------------------------------------------------------------------
// AML private endpoint (groupId 'amlworkspace') — sole inbound path to the Hub.
// Resolves both the api and notebooks private-link planes.
// ---------------------------------------------------------------------------
resource foundryHubPe 'Microsoft.Network/privateEndpoints@2023-11-01' = if (foundryHubEnabled) {
  name: '${prefix}-foundry-pe'
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [
      {
        name: '${prefix}-foundry-plsc'
        properties: {
          privateLinkServiceId: foundryHub.id
          groupIds: ['amlworkspace']
        }
      }
    ]
  }
}

resource foundryHubDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (foundryHubEnabled) {
  parent: foundryHubPe
  name: 'amlDnsGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-api-azureml'
        properties: { privateDnsZoneId: amlApiPrivateDnsZoneId }
      }
      {
        name: 'privatelink-notebooks'
        properties: { privateDnsZoneId: amlNotebooksPrivateDnsZoneId }
      }
    ]
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
