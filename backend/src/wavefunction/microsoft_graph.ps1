param([Parameter(Mandatory=$true)][string]$ModulePath)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Import-Module $ModulePath
# Load the public credential types in the SDK's dependency context. Add-Type creates
# a second copy in PowerShell's default context and breaks the SDK's MSAL interfaces.
$sdkContext=[Runtime.Loader.AssemblyLoadContext]::All | Where-Object Name -eq 'msgraph-load-context'
$references=[Microsoft.Graph.PowerShell.Authentication.Core.Utilities.AuthenticationHelpers].Assembly.GetReferencedAssemblies()
$sdkIdentity=$sdkContext.LoadFromAssemblyName(($references | Where-Object Name -eq 'Azure.Identity'))
$sdkCore=$sdkContext.LoadFromAssemblyName(($references | Where-Object Name -eq 'Azure.Core'))
$RecordType=$sdkIdentity.GetType('Azure.Identity.AuthenticationRecord')
$OptionsType=$sdkIdentity.GetType('Azure.Identity.InteractiveBrowserCredentialOptions')
$CacheType=$sdkIdentity.GetType('Azure.Identity.TokenCachePersistenceOptions')
$CredentialType=$sdkIdentity.GetType('Azure.Identity.InteractiveBrowserCredential')
$ContextType=$sdkCore.GetType('Azure.Core.TokenRequestContext')

function Respond($value) {
    [Console]::WriteLine('WAVEFUNCTION_GRAPH:' + (ConvertTo-Json -InputObject $value -Depth 100 -Compress))
}

try {
    # JSON on stdin is data; account names, IDs and document content are never PowerShell code.
    $request = [Console]::ReadLine() | ConvertFrom-Json -AsHashtable
    if ($request.operation -eq 'sign_in') {
        Connect-MgGraph -Scopes $request.scopes -TenantId $request.tenant -LoginHint $request.email -ContextScope CurrentUser -NoWelcome -ClientTimeout 30 | ForEach-Object {
            # The SDK can fall back to device authentication if the browser cannot open.
            if ($_ -is [string] -and $_ -match 'code\s+([A-Z0-9-]+)') {
                Respond @{type='device_code'; code=$Matches[1]}
            }
        }
        if ((Get-MgContext).ContextScope -ne 'CurrentUser') { throw 'WAVEFUNCTION_CACHE_UNAVAILABLE' }
        $profile = Invoke-MgGraphRequest -Method GET -Uri 'https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName,displayName' -OutputType PSObject
        # SDK 2.39 stores a separate public Azure.Identity authentication record per login hint.
        # This is identity metadata, not an access/refresh token. The SDK owns the secure cache.
        $key = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($request.email.Trim().ToLowerInvariant()))).ToLowerInvariant()
        $recordPath = Join-Path ([Environment]::GetFolderPath('UserProfile')) ".mg/mg.authrecord.$key.json"
        $record = [IO.File]::ReadAllText($recordPath)
        Respond @{type='result'; profile=$profile; record=$record}
        exit 0
    }

    $saved = Get-Content -LiteralPath $request.account_path -Raw | ConvertFrom-Json -AsHashtable
    $stream = [IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes($saved.record))
    try { $record = $RecordType::Deserialize($stream) } finally { $stream.Dispose() }
    $options = $OptionsType::new()
    $options.AuthenticationRecord = $record
    $options.ClientId = $record.ClientId
    $options.TenantId = $record.TenantId
    $options.TokenCachePersistenceOptions = $CacheType::new()
    $options.TokenCachePersistenceOptions.Name = 'mg.msal.cache'
    # Use the SDK's bundled, public Azure.Identity API for silent renewal. Connect-MgGraph
    # alone may open a browser on expiry, which background agent calls must never do.
    $options.DisableAutomaticAuthentication = $true
    $credential = $CredentialType::new($options)
    $scopes = if ($request.operation -eq 'reuse_sign_in') { $request.scopes } else { $saved.scopes }
    $context = $ContextType::new([string[]]$scopes, $null, $null, $record.TenantId, $true)
    $token = $credential.GetToken($context, [Threading.CancellationToken]::None)
    Connect-MgGraph -AccessToken (ConvertTo-SecureString $token.Token -AsPlainText -Force) -NoWelcome -ClientTimeout 30 | Out-Null
    # Verify the stable Graph ID before every operation, including writes. Never infer
    # account identity from a JWT: Microsoft personal tokens can be opaque.
    $profileUri = if ($request.operation -eq 'reuse_sign_in') { 'https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName,displayName' } else { 'https://graph.microsoft.com/v1.0/me?$select=id' }
    $profile = Invoke-MgGraphRequest -Method GET -Uri $profileUri -OutputType PSObject
    if ($profile.id -ne $saved.user_id) { throw 'WAVEFUNCTION_ACCOUNT_MISMATCH' }
    if ($request.operation -eq 'reuse_sign_in') {
        Respond @{type='result'; profile=$profile; record=$saved.record}
        exit 0
    }
    $uri = [Uri]$request.uri
    if ($uri.Scheme -ne 'https' -or $uri.Host -ne 'graph.microsoft.com' -or -not $uri.AbsolutePath.StartsWith('/v1.0/')) { throw 'WAVEFUNCTION_INVALID_URI' }
    $params = @{Method=$request.method; Uri=$uri.AbsoluteUri; OutputType='PSObject'}
    if ($request.ContainsKey('body')) {
        $params.Body = ConvertTo-Json -InputObject $request.body -Depth 100 -Compress
        $params.ContentType = 'application/json'
    }
    if ($request.ContainsKey('text')) {
        $params.Body = [Text.Encoding]::UTF8.GetBytes($request.text)
        $params.ContentType = 'text/plain; charset=utf-8'
    }
    if ($request.ContainsKey('content_base64')) {
        $params.Body = [Convert]::FromBase64String($request.content_base64)
        $params.ContentType = 'application/octet-stream'
    }
    if ($request.download_to) {
        # Streamed straight into the file the app named, 64 KB at a time: neither this helper
        # nor the app holds the document in memory, and no size is refused here.
        $response = Invoke-MgGraphRequest -Method GET -Uri $uri.AbsoluteUri -OutputType HttpResponseMessage
        $source = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $destination = [IO.File]::Create($request.download_to)
        $written = 0L
        try {
            $buffer = [byte[]]::new(65536)
            while (($count = $source.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $destination.Write($buffer, 0, $count)
                $written += $count
            }
        } finally { $source.Dispose(); $destination.Dispose(); $response.Dispose() }
        Respond @{type='result'; data=@{bytes=$written}}
    } else {
        $data = Invoke-MgGraphRequest @params
        Respond @{type='result'; data=$data}
    }
} catch {
    # SDK error text can contain request details; only structured classifications leave this process.
    $message = $_.Exception.ToString()
    $kind = 'request_failed'
    if ($message -match 'AuthenticationRequired|MsalUiRequired|invalid_grant|AADSTS700|AADSTS50173|AADSTS50076|Status:\s*401|Unauthorized') { $kind='sign_in_required' }
    elseif ($message -match 'AADSTS65001|AADSTS90094|AADSTS65004|consent_required|AADSTS53003') { $kind='approval_required' }
    elseif ($message -match 'WAVEFUNCTION_ACCOUNT_MISMATCH') { $kind='account_mismatch' }
    elseif ($message -match 'WAVEFUNCTION_CACHE_UNAVAILABLE') { $kind='cache_unavailable' }
    elseif ($message -match '403|Forbidden|ErrorAccessDenied') { $kind='access_denied' }
    elseif ($message -match '404|NotFound|itemNotFound') { $kind='not_found' }
    elseif ($message -match '429|TooManyRequests') { $kind='throttled' }
    Respond @{type='error'; error=$kind}
    exit 1
}
