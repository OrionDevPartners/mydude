// MyDude — Azure MCP Dev Accelerator
// An Azure Container App that runs the governed FastMCP server
// (src/mcp/azure_dev_server.py). The bearer token is sourced from Key Vault at
// runtime under the supplied user-assigned identity, never injected as a Bicep
// secret (pillar #3).
//
// Posture (parameterized — the GOVERNANCE DEFAULT stays VNet-internal):
//   * externalIngress=false (default): the managed environment is VNet-internal
//     (internal:true) and ingress is internal-only (external:false). The endpoint
//     is reachable only from inside the VNet; bearer auth + host pinning guard it.
//   * externalIngress=true: the managed environment is external (internal:false,
//     still VNet-integrated for private egress to Cosmos/Postgres/AOAI) and ingress
//     is public (external:true). Set `customDomain` to bind a public custom domain
//     with an Azure-managed TLS certificate. In this posture the bearer token is the
//     SOLE gate, so host pinning (allowedHosts) is REQUIRED, not advisory.
//
// The aca-subnet (10.10.1.0/24) is already delegated to Microsoft.App/environments
// in network.bicep and hosts this managed environment's infrastructure.

targetScope = 'resourceGroup'

param location string
param prefix string
param tags object

@description('Resource id of the VNet subnet delegated to Microsoft.App/environments.')
param acaSubnetId string

@description('Name of the Log Analytics workspace the managed environment streams logs to.')
param logAnalyticsWorkspaceName string

@description('User-assigned identity (resource id) the container runs as — needs Key Vault get plus the data-plane RBAC for the tools it exposes (Cosmos read, Postgres, AOAI).')
param userAssignedIdentityId string

@description('Fully-qualified container image, e.g. myregistry.azurecr.io/mydude-azure-mcp:2026-06-14. REQUIRED.')
param containerImage string

@description('Container registry login server for managed-identity pull, e.g. myregistry.azurecr.io. REQUIRED.')
param containerRegistryServer string

@description('Azure subscription id the server resolves ARM outputs / Key Vault from.')
param subscriptionId string = subscription().subscriptionId

@description('Key Vault secret name holding the MCP bearer token (the value lives only in Key Vault).')
param authSecretName string = 'azure-mcp-auth-token'

@description('Key Vault secret name holding the two-phase deploy-token SIGNING secret (the value lives only in Key Vault; the container fetches it at runtime under its identity). Required for azure_deploy_plan/apply to mint+verify plan tokens.')
param deployTokenSecretName string = 'azure-mcp-deploy-token-secret'

@description('Enable the BILLABLE two-phase deploy APPLY tool. Default false (default-deny).')
param enableAzureDeploy bool = false

@description('Host allow-list (comma-separated) for the MCP DNS-rebinding (Host-header) check. The Container App FQDN is only known AFTER the first deploy, so leave this EMPTY on the first deploy — internal ingress + mandatory bearer auth already guard the endpoint, and the host check is opted out. On the SECOND (hardening) deploy, set this to the app FQDN (deployment output azureMcpUrl host) to pin the server to its own address; the opt-out is then dropped automatically. PUBLIC posture (externalIngress=true): pinning is REQUIRED from the FIRST public deploy (phase 1) — set this to the custom domain (e.g. MydudeMCP.com) even before the domain is bound; the host-check opt-out is never taken in public mode and the deploy preflight fails loud if it is empty.')
param allowedHosts string = ''

@description('Posture toggle. false (default, GOVERNANCE DEFAULT): VNet-internal managed environment + internal ingress (reachable only inside the VNet). true: external managed environment + public ingress (reachable from the internet) — the bearer token becomes the SOLE gate, so set `allowedHosts` to pin the host. The managed environment\'s `internal` flag is IMMUTABLE post-create: flipping this on an environment that already exists requires deleting and recreating the (stateless) MCP env+app.')
param externalIngress bool = false

@description('Public custom domain to bind to the app ingress with an Azure-managed TLS certificate (e.g. MydudeMCP.com). Requires externalIngress=true. Leave EMPTY to use the default *.azurecontainerapps.io FQDN. Two-phase: deploy once with this EMPTY to obtain managedEnvStaticIp + customDomainVerificationId, create the DNS records (apex A-record -> static IP, TXT asuid.<domain> -> verification id), then deploy again with this set to mint + bind the managed certificate.')
param customDomain string = ''

@description('Domain-control validation method for the managed certificate. TXT (default) suits an APEX domain (A-record + asuid TXT). Use CNAME for a subdomain (CNAME -> app FQDN), or HTTP for token-over-HTTP validation.')
@allowed([
  'TXT'
  'CNAME'
  'HTTP'
])
param domainControlValidation string = 'TXT'

@description('Container CPU cores.')
param cpu string = '0.5'

@description('Container memory.')
param memory string = '1Gi'

@description('Min replicas (set 0 to allow scale-to-zero).')
param minReplicas int = 1

@description('Max replicas.')
param maxReplicas int = 2

// Existing Log Analytics workspace — read its customerId + shared key for the
// managed environment's log sink. The key stays inside the ARM template plane.
resource la 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

// User-assigned identity (existing) — referenced for its clientId so
// DefaultAzureCredential inside the container binds to the right MI.
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: last(split(userAssignedIdentityId, '/'))
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-mcp-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: la.properties.customerId
        sharedKey: la.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: acaSubnetId
      // GOVERNANCE DEFAULT internal:true. externalIngress=true flips this to a
      // public (internet-reachable) environment that is STILL VNet-integrated for
      // private egress. NOTE: `internal` is immutable after the environment is
      // created — changing posture on an existing env requires delete + recreate.
      internal: !externalIngress
    }
    zoneRedundant: false
  }
}

// Azure-managed TLS certificate for the public custom domain (free, auto-renewed).
// Only minted when a custom domain is supplied (public posture). The DNS records
// (apex A-record -> env.staticIp, TXT asuid.<domain> -> app.customDomainVerificationId)
// MUST already exist and validate before this deploy, hence the two-phase flow.
resource cert 'Microsoft.App/managedEnvironments/managedCertificates@2024-03-01' = if (!empty(customDomain)) {
  parent: env
  name: '${prefix}-mcp-cert'
  location: location
  tags: tags
  properties: {
    subjectName: customDomain
    domainControlValidation: domainControlValidation
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-azure-mcp'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        // GOVERNANCE DEFAULT external:false (internal). externalIngress=true makes
        // the endpoint publicly reachable; bearer auth + host pinning are then the
        // only guards. TLS is always enforced (allowInsecure:false).
        external: externalIngress
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
        // Bind the custom domain to the managed certificate. Guarded by the SAME
        // predicate as the `cert` resource (!empty(customDomain)), so cert.id is
        // only referenced when the certificate is actually deployed. (A conditional
        // resource reference may surface a benign BCP318 warning — expected here.)
        customDomains: empty(customDomain) ? [] : [
          {
            name: customDomain
            certificateId: cert.id
            bindingType: 'SniEnabled'
          }
        ]
      }
      registries: [
        {
          server: containerRegistryServer
          identity: userAssignedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'azure-mcp'
          image: containerImage
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            { name: 'AZURE_SUBSCRIPTION_ID', value: subscriptionId }
            { name: 'AZURE_CLIENT_ID', value: uami.properties.clientId }
            { name: 'ENABLE_AZURE_MCP', value: 'true' }
            { name: 'ALLOW_AZURE_DEPLOY', value: enableAzureDeploy ? 'true' : 'false' }
            { name: 'AZURE_MCP_AUTH_SECRET_NAME', value: authSecretName }
            // NAME only (never the value): the server fetches the deploy-token
            // signing secret from Key Vault at runtime under its UAMI (pillar #3),
            // so the plan->apply token binding survives restarts/replicas.
            { name: 'AZURE_MCP_DEPLOY_SECRET_NAME', value: deployTokenSecretName }
            { name: 'AZURE_MCP_PORT', value: '8080' }
            { name: 'AZURE_MCP_HOST', value: '0.0.0.0' }
            // DNS-rebinding (Host-header) hardening. The app FQDN is only known
            // AFTER the first deploy, so on the first INTERNAL deploy `allowedHosts`
            // is empty: the host check is opted out (internal ingress + mandatory
            // bearer auth + the private VNet already guard the endpoint). On the
            // SECOND deploy, pass `allowedHosts` = the app FQDN (output
            // azureMcpUrl host) to PIN the server to its own address; the
            // opt-out is then dropped (set to 'false') and only that host passes
            // — the server honours the allow-list via transport_security_from_env.
            // PUBLIC posture (externalIngress=true) is the SOLE-gate case: the
            // opt-out is NEVER taken regardless of `allowedHosts`, so an empty
            // allow-list can never silently expose the public FQDN with rebinding
            // protection off — it stays locked (SDK default, localhost only) until
            // a host is pinned. The deploy preflight (deploy.py validate_mcp_posture)
            // additionally FAILS LOUD when externalIngress=true without a host pin.
            { name: 'AZURE_MCP_ALLOWED_HOSTS', value: allowedHosts }
            { name: 'AZURE_MCP_DISABLE_HOST_CHECK', value: (empty(allowedHosts) && !externalIngress) ? 'true' : 'false' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8080 }
              initialDelaySeconds: 15
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: { path: '/healthz', port: 8080 }
              initialDelaySeconds: 10
              periodSeconds: 15
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output managedEnvironmentName string = env.name
output containerAppName string = app.name
output containerAppFqdn string = app.properties.configuration.ingress.fqdn
// Default *.azurecontainerapps.io URL (internal FQDN when externalIngress=false,
// public FQDN when true). Use customDomainUrl once the custom domain is bound.
output containerAppUrl string = 'https://${app.properties.configuration.ingress.fqdn}/mcp'
// Public custom-domain URL (empty until a customDomain is bound).
output customDomainUrl string = empty(customDomain) ? '' : 'https://${customDomain}/mcp'
// DNS setup values for the public custom domain (phase 1 -> create records -> phase 2):
//   apex A-record:  <customDomain>        -> managedEnvStaticIp
//   TXT record:     asuid.<customDomain>  -> customDomainVerificationId
output managedEnvStaticIp string = env.properties.staticIp
output customDomainVerificationId string = app.properties.customDomainVerificationId
