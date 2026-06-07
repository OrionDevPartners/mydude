// MyDude — Log Analytics + Azure Monitor + MLflow

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 90
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    workspaceCapping: {
      dailyQuotaGb: 5
    }
  }
}

// Custom metrics for MyDude governance
resource providerLatencyMetric 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-provider-latency-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Alert when provider latency exceeds 30s'
    severity: 2
    enabled: true
    scopes: [resourceGroup().id]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          criterionType: 'StaticThresholdCriterion'
          name: 'HighProviderLatency'
          metricName: 'provider_latency_ms'
          operator: 'GreaterThan'
          threshold: 30000
          timeAggregation: 'Average'
        }
      ]
    }
    actions: []
  }
}

// Application Insights for the BCS gate and fan-out gateway
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-appinsights'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    RetentionInDays: 90
  }
}

output logAnalyticsWorkspaceId string = logAnalytics.id
output logAnalyticsWorkspaceName string = logAnalytics.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
