// MyDude — Unity Catalog substrate (Databricks workspace)
//
// Unity Catalog is the claim-ledger authority. It is:
//   - Provisioned here as a Databricks workspace (hosts Unity Catalog metastore)
//   - Written ONLY by the BCS gate via the managed identity bcs_gate_identity
//   - Read-only for all other identities (projections, agent runtime, search)
//   - Network-isolated: private endpoint, no public access
//
// After Bicep provisioning the unity_migrator.py submits the DDL through
// the BCS gate (POST /claims/migration with authority=unity). The gate then
// writes to the catalog via the Unity REST API using the managed identity token.
//
// AUTHORITY RULE: Unity Catalog is the ledger — it stores promoted claims.
//   It is NOT a routing authority and does NOT own Postgres DDL.
//   The V5 scope gate and authority boundary in base.py enforce this separation.

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object
param bcsGatePrincipalId string     // sole catalog writer
param readonlyPrincipalId string    // reader identity (projections, search)
param peSubnetId string
param vnetId string
param storageAccountId string       // ADLS Gen2 account used as Unity Catalog root storage
param dbrPublicSubnetName string    // from network.bicep output (delegated to Databricks)
param dbrPrivateSubnetName string   // from network.bicep output (delegated to Databricks)
param dbrPrivateDnsZoneId string    // privatelink.azuredatabricks.net zone ID from network.bicep

// ---------------------------------------------------------------------------
// Databricks workspace — hosts Unity Catalog metastore
// ---------------------------------------------------------------------------
resource databricksWorkspace 'Microsoft.Databricks/workspaces@2023-02-01' = {
  name: '${prefix}-databricks'
  location: location
  tags: union(tags, {
    role: 'unity-catalog-host'
    authority: 'claim-ledger-only'
    catalog_write_identity: 'bcs_gate_identity'
  })
  sku: {
    name: 'premium'     // Premium SKU required for Unity Catalog
  }
  properties: {
    managedResourceGroupId: '${subscription().id}/resourceGroups/MyDude-dbr-managed'
    publicNetworkAccess: 'Disabled'
    requiredNsgRules: 'NoAzureDatabricksRules'  // private link only
    parameters: {
      enableNoPublicIp: {
        value: true
      }
      customVirtualNetworkId: {
        value: vnetId
      }
      customPublicSubnetName: {
        value: dbrPublicSubnetName   // defined in network.bicep, delegated to Microsoft.Databricks/workspaces
      }
      customPrivateSubnetName: {
        value: dbrPrivateSubnetName  // defined in network.bicep, delegated to Microsoft.Databricks/workspaces
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Private endpoint for Databricks workspace
// ---------------------------------------------------------------------------
resource databricksPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${prefix}-dbr-pe'
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [
      {
        name: '${prefix}-dbr-plsc'
        properties: {
          privateLinkServiceId: databricksWorkspace.id
          groupIds: ['databricks_ui_api']
        }
      }
    ]
  }
}

// DNS zone group for Databricks UI/API private endpoint.
// Required so VNet clients resolve <workspace>.azuredatabricks.net to the private IP.
resource databricksDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: databricksPrivateEndpoint
  name: 'dbrDnsGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-azuredatabricks'
        properties: {
          privateDnsZoneId: dbrPrivateDnsZoneId
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// RBAC: BCS gate → "Contributor" on Databricks workspace (required to write
// to Unity Catalog via the REST API with managed identity token).
// This is the ONLY identity with write access to the workspace.
//
// "Contributor" role ID: b24988ac-6180-42a0-ab88-20f7382dd24c
// ---------------------------------------------------------------------------
resource bcsGateDatabricksContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(databricksWorkspace.id, bcsGatePrincipalId, 'b24988ac-6180-42a0-ab88-20f7382dd24c')
  scope: databricksWorkspace
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'b24988ac-6180-42a0-ab88-20f7382dd24c'
    )
    principalId: bcsGatePrincipalId
    principalType: 'ServicePrincipal'
    description: 'BCS gate: sole Unity Catalog writer. No other identity has Contributor on this workspace.'
  }
}

// ---------------------------------------------------------------------------
// RBAC: readonly identity → "Reader" on Databricks workspace
// Used by Master_DB, Fan-out Gateway, and AI Search projection sync.
// "Reader" role ID: acdd72a7-3385-48ef-bd42-f606fba81ae7
// ---------------------------------------------------------------------------
resource readonlyDatabricksReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(databricksWorkspace.id, readonlyPrincipalId, 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
  scope: databricksWorkspace
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'acdd72a7-3385-48ef-bd42-f606fba81ae7'
    )
    principalId: readonlyPrincipalId
    principalType: 'ServicePrincipal'
    description: 'Readonly identity: catalog read (projections, search). No write access.'
  }
}

// ---------------------------------------------------------------------------
// Post-provisioning: Unity Catalog metastore and catalog must be initialised
// via the Databricks REST API — this cannot be done via Bicep alone.
// The provisioning steps are in PROVISIONING.md §4b.
// The unity_migrator.py then applies the DDL through the BCS gate.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
output databricksWorkspaceId string = databricksWorkspace.id
output databricksWorkspaceUrl string = 'https://${databricksWorkspace.properties.workspaceUrl}'
output databricksManagedResourceGroupId string = '${subscription().id}/resourceGroups/MyDude-dbr-managed'
output unityCatalogEndpoint string = 'https://${databricksWorkspace.properties.workspaceUrl}'
