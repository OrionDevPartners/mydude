// MyDude — Microsoft Fabric capacity (OneLake lakehouse for the domain-knowledge corpus).
//
// AUTHORITY: Fabric/OneLake holds the LARGE domain-knowledge corpus at scale (the
// lakehouse). Cosmos holds low-latency agent memory; Postgres holds relational/
// governance/audit/secrets. This module provisions the Fabric CAPACITY (the compute +
// billing unit). Workspaces, lakehouse items, and OneLake shortcuts are created
// post-deploy (Fabric is SaaS; those are control-plane/admin actions, not ARM resources).
//
// COST: F-SKU capacity is billed per hour while RUNNING. It can be PAUSED to stop
// compute billing. Choose the smallest SKU that meets throughput and scale up as needed.
//
// PRIVATE LINK: Fabric private endpoints / tenant inbound-access settings are tenant-
// and admin-scoped and are NOT expressible from this RG-scoped template. Treat
// private-only hardening of Fabric as a post-deploy admin step (see PROVISIONING.md).

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object

@description('Fabric capacity SKU (F2, F4, F8, F16, F32, F64, ...). Billed per-hour while running; pause to stop billing.')
param fabricSkuName string = 'F32'

@description('Fabric capacity admin members — UPNs/emails or AAD object IDs. At least one is REQUIRED.')
param fabricAdminMembers array

resource capacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: '${prefix}fabric'
  location: location
  tags: tags
  sku: {
    name: fabricSkuName
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: fabricAdminMembers
    }
  }
}

output fabricCapacityName string = capacity.name
output fabricCapacityId string = capacity.id
output fabricSku string = fabricSkuName
