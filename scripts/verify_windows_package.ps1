param([Parameter(Mandatory=$true)][string]$Package, [Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
$taskOutput = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
$taskMove = Join-Path $taskOutput '移动后的中文目录 portable test'
Expand-Archive -LiteralPath $Package -DestinationPath $taskMove
$taskBundle = Join-Path $taskMove 'Zhanguo-Windows-x64'
$taskExe = Join-Path $taskBundle 'Zhanguo.exe'
# A source checkout, configured model, installed interpreter or SDK must not
# silently supply a missing part of the portable package.
$env:ZHANGUO_ROOT = 'Z:\unavailable-source'
$env:ZHANGUO_PYTHON = 'Z:\unavailable-python.exe'
$env:ZHANGUO_DESKTOP_CONFIG = 'Z:\unavailable-private-config.json'
$env:ZHANGUO_RESUME = 'Z:\unavailable-private-save.json'
$env:DOTNET_ROOT = 'Z:\unavailable-dotnet'
$env:DOTNET_ROOT_X64 = 'Z:\unavailable-dotnet'
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"

function Run-GameCheck([string]$Name, [string[]]$GameArguments, [int]$Timeout = 180000) {
    $taskStdout = Join-Path $taskOutput "$Name.stdout.txt"
    $taskStderr = Join-Path $taskOutput "$Name.stderr.txt"
    $taskQuoted = (@('--audio-driver', 'Dummy') + $GameArguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' '
    $taskProc = Start-Process -FilePath $taskExe -ArgumentList $taskQuoted -WorkingDirectory $env:SystemRoot -PassThru `
        -RedirectStandardOutput $taskStdout -RedirectStandardError $taskStderr
    if (-not $taskProc.WaitForExit($Timeout)) { $taskProc.Kill($true); throw "$Name timed out" }
    if ($taskProc.ExitCode -ne 0) {
        Get-Content $taskStdout; Get-Content $taskStderr
        throw "$Name failed: $($taskProc.ExitCode)"
    }
    if ([regex]::IsMatch([IO.File]::ReadAllText($taskStderr), '(?m)^ERROR:')) {
        Get-Content $taskStderr
        throw "$Name logged an engine/runtime error despite exiting successfully"
    }
}

$taskCheck = Join-Path $taskOutput 'portable-check.json'
Run-GameCheck 'portable' @('--headless', '--', '--portable-check', $taskCheck)
$taskReport = Get-Content -Raw $taskCheck | ConvertFrom-Json
if (-not $taskReport.packaged -or $taskReport.host_exit -ne 0 -or $taskReport.period -ne 1) { throw 'Invalid portable verification report' }

# ANGLE can render on the runner's Direct3D device. Validate a real window as
# well as the headless .NET/Python check; the distributed default stays OpenGL.
Run-GameCheck 'menu' @('--rendering-method', 'gl_compatibility', '--rendering-driver', 'opengl3_angle', '--', '--menu-output', (Join-Path $taskOutput 'menu.png'))
if (-not (Test-Path (Join-Path $taskOutput 'menu.png'))) { throw 'Exported menu did not produce a rendered screenshot' }
$env:ZHANGUO_STARTUP_FIXTURES = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../tests/fixtures'))
Run-GameCheck 'startup' @('--rendering-method', 'gl_compatibility', '--rendering-driver', 'opengl3_angle', '--', '--startup-review-output', (Join-Path $taskOutput 'startup.png')) 300000
$taskStartup = Get-Content -Raw (Join-Path $taskOutput 'startup.png.json') | ConvertFrom-Json
foreach ($taskCheck in @('edition_selection', 'real_feature_switches', 'model_preflight', 'registered_clients', 'save_copy_upgrade', 'originals_unchanged', 'audit_preserved', 'routes_preserved', 'scripts_preserved', 'resume_uses_save_rules', 'actual_game_rules')) {
    if ($taskStartup.$taskCheck -ne $true) { throw "Startup/rules/save verification failed: $taskCheck" }
}
if ($taskStartup.layouts.Count -ne 4) { throw 'Startup layout verification incomplete' }
Run-GameCheck 'world' @('--rendering-method', 'gl_compatibility', '--rendering-driver', 'opengl3_angle', '--', '--smoke-output', (Join-Path $taskOutput 'world.png')) 300000
$taskWorld = Get-Content -Raw (Join-Path $taskOutput 'world.png.json') | ConvertFrom-Json
if ($taskWorld.period -lt 3 -or $taskWorld.artwork -ne 'unified-pixel-world' -or -not $taskWorld.readable_font) { throw 'Exported game did not load the current artwork/font/host' }
if (-not $taskWorld.pause_menu_checked -or $taskWorld.action_batches -lt 24 -or $taskWorld.settlement_batches -lt 3 -or $taskWorld.settlement_marches -ne 0) { throw 'Call playback or pause menu verification failed' }
if ($taskWorld.played_phases.production -ne 24 -or -not $taskWorld.ledger_within_viewport -or $taskWorld.ledger_rows -ne 7) { throw 'National production or single-nation ledger verification failed' }
if ($taskWorld.markets -lt 8 -or $taskWorld.road_tiles -lt 1 -or -not $taskWorld.national_cloth_checked) { throw 'Markets, roads or national clothing verification failed' }
Run-GameCheck 'effects' @('--rendering-method', 'gl_compatibility', '--rendering-driver', 'opengl3_angle', '--', '--effects-review-output', (Join-Path $taskOutput 'effects.png')) 300000
$taskEffects = Get-Content -Raw (Join-Path $taskOutput 'effects.png.json') | ConvertFrom-Json
if (-not $taskEffects.offline -or -not $taskEffects.national_colors_checked -or $taskEffects.checks.Count -lt 9) { throw 'Trade, letter, battle arrow or bird verification failed' }
$env:ZHANGUO_LEDGER_REVIEW = (Get-ChildItem (Join-Path $taskOutput 'effects-fixture-*.json') | Select-Object -First 1).FullName
Run-GameCheck 'ledger' @('--rendering-method', 'gl_compatibility', '--rendering-driver', 'opengl3_angle', '--', '--ledger-review-output', (Join-Path $taskOutput 'ledger.png')) 300000
$taskLedger = Get-Content -Raw (Join-Path $taskOutput 'ledger.png.json') | ConvertFrom-Json
if ($taskLedger.Count -ne 12 -or ($taskLedger | Where-Object { -not $_.ledger_within_viewport -or $_.overlap })) { throw 'Single-country ledger font/scale/layout verification failed' }
Run-GameCheck 'human' @('--rendering-method', 'gl_compatibility', '--rendering-driver', 'opengl3_angle', '--', '--human-review-output', (Join-Path $taskOutput 'human.png')) 900000
$taskHuman = Get-Content -Raw (Join-Path $taskOutput 'human.png.json') | ConvertFrom-Json
foreach ($taskCheck in @('rts_icons', 'two_step_player_menu', 'custom_nation_name_and_color', 'tile_a_b_a', 'disabled_hover', 'short_hover', 'long_hover', 'construction_quota_disabled', 'rts_layouts')) {
    if ($taskHuman.$taskCheck -ne $true) { throw "RTS human interaction verification failed: $taskCheck" }
}
foreach ($taskCheck in @('market_quantity_buttons', 'market_exact_quotes', 'auto_trade_buy_and_sell', 'auto_trade_validation', 'cancel_half_refund', 'cancel_keeps_quota', 'demolish_zero_refund', 'market_layouts', 'garrison_visible_without_scroll', 'wild_garrison_continuity')) {
    if ($taskHuman.$taskCheck -ne $true) { throw "Market/demolition verification failed: $taskCheck" }
}
foreach ($taskCheck in @('power_visible', 'power_ignores_unfinished', 'power_previous_actual')) {
    if ($taskHuman.$taskCheck -ne $true) { throw "Power display verification failed: $taskCheck" }
}
foreach ($taskCheck in @('outliner_tabs', 'outliner_filters', 'cross_site_selection', 'mouse_orders', 'shift_waypoints', 'selection_halos', 'march_order_badges', 'numbers_without_enter', 'market_reference_visible', 'resource_breakdown_hover', 'queued_order_cancel', 'human_selected_speed')) {
    if ($taskHuman.$taskCheck -ne $true) { throw "Outliner/input/resource verification failed: $taskCheck" }
}
foreach ($taskCheck in @('simple_terrain_map', 'terrain_preserves_fog', 'terrain_price_steps', 'free_space_filter', 'compact_land_icons', 'right_return_preferences', 'right_return_army_priority', 'right_return_last_button', 'guide_api_and_return')) {
    if ($taskHuman.$taskCheck -ne $true) { throw "Terrain/filter/guide verification failed: $taskCheck" }
}
if ($taskHuman.period -lt 3 -or $taskHuman.layouts.Count -ne 4 -or -not $taskHuman.normal_zero_army_start -or -not $taskHuman.repeated_market_clicks -or -not $taskHuman.build_and_quote -or -not $taskHuman.recruit -or -not $taskHuman.move_did_not_occupy -or -not $taskHuman.in_place_attack -or -not $taskHuman.map_purchase -or -not $taskHuman.historical_pixels_static -or -not $taskHuman.single_nation_ledger -or -not $taskHuman.save_queued -or -not $taskHuman.exit_choice -or -not $taskHuman.esc_cancels_target -or -not $taskHuman.war_warning -or -not $taskHuman.letter_form -or -not $taskHuman.production_queue -or -not $taskHuman.pending_full_refund) { throw 'Single local human verification failed' }
$taskExtinctionFixture = Join-Path $taskOutput 'extinction-fixture.json'
$env:ZHANGUO_EXTINCTION_REVIEW = $taskExtinctionFixture
Push-Location (Join-Path $taskBundle 'game')
try {
    & (Join-Path $taskBundle 'runtime/python/python.exe') -X utf8 -m mods.ui.desktop_review --extinction --output $taskExtinctionFixture
    if ($LASTEXITCODE -ne 0) { throw 'Could not generate the offline extinction fixture' }
} finally { Pop-Location }
Run-GameCheck 'extinction' @('--rendering-method', 'gl_compatibility', '--rendering-driver', 'opengl3_angle', '--', '--extinction-review-output', (Join-Path $taskOutput 'extinction.png'))
$taskExtinction = Get-Content -Raw (Join-Path $taskOutput 'extinction.png.json') | ConvertFrom-Json
if ($taskExtinction.Count -ne 3 -or $taskExtinction[0].owner -ne 'scenario:other' -or -not $taskExtinction[0].color) { throw 'Extinction review did not begin with the old B map' }
foreach ($taskStage in $taskExtinction | Select-Object -Skip 1) {
    if ($taskStage.owner -or $taskStage.color -or $taskStage.caption -notlike '*现归属待确认*') { throw 'Extinct ownership was not invalidated after settlement/reload' }
}
Write-Output 'WINDOWS_PORTABLE_VERIFIED'
