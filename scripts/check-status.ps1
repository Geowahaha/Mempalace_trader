param(
    [string]$Url = "http://127.0.0.1:8080/status"
)

$ErrorActionPreference = "Stop"
Invoke-RestMethod -Uri $Url -Method Get | ConvertTo-Json -Depth 6
