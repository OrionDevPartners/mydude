// MyDude — PostgreSQL Flexible Server
// Hosts: agents_home (routing authority) + provider_home (candidate cognition + outbox)
// Each database has its own role, credentials, and migration lineage.

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object

@secure()
param adminPassword string

param delegatedSubnetId string
param privateDnsZoneId string

resource pgServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: '${prefix}-pg'
  location: location
  tags: tags
  sku: {
    name: 'Standard_D4ds_v5'
    tier: 'GeneralPurpose'
  }
  properties: {
    administratorLogin: 'mydude_admin'
    administratorLoginPassword: adminPassword
    version: '16'
    storage: {
      storageSizeGB: 128
      autoGrow: 'Enabled'
    }
    highAvailability: {
      mode: 'ZoneRedundant'
      standbyAvailabilityZone: '2'
    }
    network: {
      delegatedSubnetResourceId: delegatedSubnetId
      privateDnsZoneArmResourceId: privateDnsZoneId
    }
    backup: {
      backupRetentionDays: 35
      geoRedundantBackup: 'Enabled'
    }
  }
}

// agents_home database
resource agentsHomeDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: pgServer
  name: 'agents_home'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// provider_home database
resource providerHomeDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: pgServer
  name: 'provider_home'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// PostgreSQL configuration
resource pgConfig_sharedPreloadLibs 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: pgServer
  name: 'shared_preload_libraries'
  properties: {
    value: 'pg_stat_statements,pgaudit'
    source: 'user-override'
  }
}

resource pgConfig_logConnections 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: pgServer
  name: 'log_connections'
  properties: {
    value: 'on'
    source: 'user-override'
  }
}

output serverName string = pgServer.name
output serverFqdn string = pgServer.properties.fullyQualifiedDomainName
output agentsHomeDbName string = agentsHomeDb.name
output providerHomeDbName string = providerHomeDb.name
