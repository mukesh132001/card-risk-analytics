# Creates the roadmap issues on GitHub. Run once from the repo root:
#   gh auth login        (first time only)
#   powershell -ExecutionPolicy Bypass -File scripts/create-issues.ps1
$ErrorActionPreference = "Stop"
$issues = Get-Content -Raw "scripts/issues.json" | ConvertFrom-Json
$milestones = $issues | ForEach-Object { $_.m } | Sort-Object -Unique
foreach ($m in $milestones) {
    gh label create $m --color "0E8A16" --force | Out-Null
}
gh label create "task" --color "1D76DB" --force | Out-Null
foreach ($i in $issues) {
    gh issue create --title $i.title --body $i.body --label $i.m --label "task"
}
Write-Host "Created $($issues.Count) issues."
