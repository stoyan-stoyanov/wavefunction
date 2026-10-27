# Offline compatibility check with a synthetic identity and an isolated, empty cache.
param([Parameter(Mandatory=$true)][string]$ModuleRoot)
$ErrorActionPreference='Stop'
$moduleRoot=$ModuleRoot
Import-Module "$moduleRoot/Microsoft.Graph.Authentication.psd1"
$sdkContext=[Runtime.Loader.AssemblyLoadContext]::All | Where-Object Name -eq 'msgraph-load-context'
$references=[Microsoft.Graph.PowerShell.Authentication.Core.Utilities.AuthenticationHelpers].Assembly.GetReferencedAssemblies()
$sdkIdentity=$sdkContext.LoadFromAssemblyName(($references | Where-Object Name -eq 'Azure.Identity'))
$sdkCore=$sdkContext.LoadFromAssemblyName(($references | Where-Object Name -eq 'Azure.Core'))
$RecordType=$sdkIdentity.GetType('Azure.Identity.AuthenticationRecord')
$OptionsType=$sdkIdentity.GetType('Azure.Identity.InteractiveBrowserCredentialOptions')
$CacheType=$sdkIdentity.GetType('Azure.Identity.TokenCachePersistenceOptions')
$CredentialType=$sdkIdentity.GetType('Azure.Identity.InteractiveBrowserCredential')
$ContextType=$sdkCore.GetType('Azure.Core.TokenRequestContext')
$fixtureClient=[Guid]::NewGuid().ToString()
$fixtureTenant=[Guid]::NewGuid().ToString()
$fixtureJson=@{username='synthetic@example.test';authority='https://login.microsoftonline.com/';homeAccountId="synthetic.$fixtureTenant";tenantId=$fixtureTenant;clientId=$fixtureClient;version='1.0'} | ConvertTo-Json -Compress
$stream=[IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes($fixtureJson))
$record=$RecordType::Deserialize($stream)
$stream.Dispose()
$options=$OptionsType::new()
$options.AuthenticationRecord=$record
$options.ClientId=$record.ClientId
$options.TenantId=$record.TenantId
$options.DisableAutomaticAuthentication=$true
$options.DisableInstanceDiscovery=$true
$options.TokenCachePersistenceOptions=$CacheType::new()
$options.TokenCachePersistenceOptions.Name='wavefunction-synthetic-'+[Guid]::NewGuid().ToString()
$credential=$CredentialType::new($options)
$context=$ContextType::new([string[]]@('User.Read'),$null,$null,$record.TenantId,$true)
try { $null=$credential.GetToken($context,[Threading.CancellationToken]::None); throw 'Unexpected token for synthetic identity' }
catch { if ($_.Exception.ToString() -notmatch 'AuthenticationRequiredException') { throw }; 'PASS: missing synthetic credentials require explicit sign-in; no browser launched.' }
$errors=$null;$tokens=$null
$null=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../backend/src/wavefunction/microsoft_graph.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
'PASS: Graph bridge parses with the pinned PowerShell release.'
