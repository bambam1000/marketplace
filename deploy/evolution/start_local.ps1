# Démarre en local tout ce qu'il faut pour tester WhatsApp :
#   1. Evolution API (Docker Desktop)   2. le worker d'envoi (nouvelle fenêtre)   3. Django sur le port 8000
# Usage (PowerShell, depuis la racine du projet) :  powershell -ExecutionPolicy Bypass -File deploy\evolution\start_local.ps1
# Prérequis : Docker Desktop démarré, et deploy\evolution\.env rempli (déjà généré avec des secrets aléatoires).

$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$envFile = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path $envFile)) { throw "Fichier $envFile introuvable : copiez .env.example vers .env et remplissez-le." }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker n'est pas installé ou pas dans le PATH (installez Docker Desktop)." }

# Lit une valeur du fichier .env
function Get-EnvValue($name) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if (-not $line) { throw "$name absent de $envFile" }
    return $line.Substring($name.Length + 1).Trim()
}

Write-Host '1/3  Démarrage d''Evolution API (Docker)…' -ForegroundColor Cyan
Push-Location $PSScriptRoot
docker compose up -d
Pop-Location

Write-Host 'Attente de la réponse d''Evolution API…'
$apiKey = Get-EnvValue 'AUTHENTICATION_API_KEY'
$ready = $false
foreach ($i in 1..30) {
    try {
        $info = Invoke-RestMethod -Uri 'http://127.0.0.1:8081/' -Headers @{ apikey = $apiKey } -TimeoutSec 3
        if ("$($info.message)" -notmatch 'Evolution API') { throw "Le port 8081 est utilisé par un autre programme que Evolution API." }
        $ready = $true; break
    }
    catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) { throw "Evolution API ne répond pas sur http://127.0.0.1:8081 (voir : docker compose logs evolution-api)." }

# Variables lues par config/settings.py
$env:WHATSAPP_ENABLED = '1'
$env:EVOLUTION_API_URL = 'http://127.0.0.1:8081'
$env:EVOLUTION_API_KEY = $apiKey
$env:WHATSAPP_WEBHOOK_SECRET = Get-EnvValue 'AFRIMARKET_WEBHOOK_SECRET'
# Le conteneur joint Django sur la machine hôte via host.docker.internal
$env:WHATSAPP_WEBHOOK_URL = 'http://host.docker.internal:8000/whatsapp/webhook/'
$env:DJANGO_ALLOWED_HOSTS = '127.0.0.1,localhost,host.docker.internal'
$env:WHATSAPP_DELIVERY_MODE = 'worker'

$python = Join-Path $root '.venv\Scripts\python.exe'
Write-Host '2/3  Démarrage du worker d''envoi WhatsApp (nouvelle fenêtre)…' -ForegroundColor Cyan
Start-Process powershell -ArgumentList '-NoExit', '-Command', "Set-Location '$root'; & '$python' manage.py whatsapp_worker" -WorkingDirectory $root

Write-Host '3/3  Démarrage de Django sur http://127.0.0.1:8000 …' -ForegroundColor Cyan
Write-Host 'Ensuite : Dashboard → WhatsApp → Connexion → « Connecter WhatsApp », puis scannez le QR code.' -ForegroundColor Green
Set-Location $root
& $python manage.py runserver 0.0.0.0:8000
