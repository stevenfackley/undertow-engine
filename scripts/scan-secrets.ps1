#requires -Version 7.2
<#
.SYNOPSIS
  Secret-shape and banned-package scan, runnable locally. Same patterns, same engine (git grep)
  and same leave-outs as the shared CI workflow, gh-actions/secret-scan.yml.

.DESCRIPTION
  Scans the files git knows about in the checkout this script is in, wherever it is run from
  (-Root names another checkout): every tracked file, plus new
  files that are not ignored (what `git add -A` would stage). It asks git for them and never
  walks the disk, so:

    - no folder list has to be kept in step with the repo's layout. The old default, src and
      tests, came from a template: it missed whole folders and every file at the root, and where
      the repo had neither folder it read nothing and still reported a pass;
    - node_modules, build output and ignored local files such as .env are not read;
    - a folder named [id] or [...slug] is read like any other.

  Binary files are skipped, as they are in CI. One exception: UTF-16 and UTF-32 files, which
  git cannot search, are found by their byte-order mark, decoded and searched here.

  Exit code 0: clean. 1: a match, nothing to scan, or anything git could not read.
#>
[CmdletBinding()]
param(
    # Pathspecs relative to the repo root. The default is the whole tree.
    [string[]]$Paths = @('.'),
    # The checkout to scan: any folder inside it. The default is the one this script is in. It used
    # to be the current directory, so the script run by its full path from another repo scanned
    # that repo and reported it clean.
    [string]$Root = $PSScriptRoot,
    [string]$BannedPackages = 'ApplicationInsights|Sentry|Segment|Mixpanel|Datadog|NewRelic|Raygun|Rollbar|Bugsnag|HockeyApp|AppCenter|GoogleAnalytics',
    # Never searched for secret shapes. Same list as the shared workflow.
    [string[]]$Exclude = @('.env.example', 'docs/**', '.claude/**', '.agents/**', 'skills/**', '*scan-secrets*')
)

$ErrorActionPreference = 'Stop'
# git grep exits 1 for "no match". That must never be turned into a thrown error.
$PSNativeCommandUseErrorActionPreference = $false

$unread = [System.Collections.Generic.List[string]]::new()
$gitStderr = [System.IO.Path]::GetTempFileName()
$consoleEncoding = [Console]::OutputEncoding

function Invoke-Git {
    <#
      Runs git and returns its output lines; the exit code goes to $script:gitExit. Anything git
      writes to stderr is kept in $unread and fails the scan at the end: git grep reports a file
      it could not open there and still exits 1, which is also its "no match".

      The arguments come as one array on purpose. Written out after the function name, a bare
      `--` is taken by PowerShell as its own end-of-parameters mark and never reaches git, and
      without it git may read a path that is also a branch name as a revision.
    #>
    param([string[]]$Arguments)
    $lines = @(& git -c core.quotePath=false -c color.ui=never @Arguments 2>$gitStderr)
    $script:gitExit = $LASTEXITCODE
    $said = [System.IO.File]::ReadAllText($gitStderr).Trim()
    if ($said) { $unread.Add($said) }
    $lines
}

function Search-Tree {
    <#
      git grep over the working tree, in two calls: tracked files, then new files that are not
      ignored. One call with --untracked is not enough: it walks the disk and applies .gitignore
      to every file it meets, so it passes over a tracked file that matches an ignore rule, and a
      force-added .env is exactly that.

      Lines holds what matched. Failed is git's exit code when a call failed (anything but 0, a
      match, or 1, no match), and 0 otherwise. A failed call is a failure of the scan, never a
      pass; what an earlier call matched is still returned.
    #>
    param([string[]]$Options, [string]$Pattern, [string[]]$Spec)
    # A tracked file that is not ignored is found by both calls, so each line is kept once.
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    $lines = [System.Collections.Generic.List[string]]::new()
    $failed = 0
    foreach ($scope in @(), @('--untracked')) {
        $found = @(Invoke-Git (@('-C', $top, 'grep') + $Options + $scope + @('-e', $Pattern, '--') + $Spec))
        if ($gitExit -eq 0) {
            foreach ($line in $found) { if ($seen.Add($line)) { $lines.Add($line) } }
        }
        elseif ($gitExit -ne 1) {
            $failed = $gitExit
            break
        }
    }
    [pscustomobject]@{ Failed = $failed; Lines = $lines.ToArray() }
}

function Test-WideBom {
    # True when the file starts with a UTF-16 or UTF-32 byte-order mark.
    param([string]$File)
    if (-not [System.IO.File]::Exists($File)) { return $false }   # deleted in the working tree, or a submodule
    $bytes = [byte[]]::new(4)
    try {
        $stream = [System.IO.File]::Open($File, 'Open', 'Read', 'ReadWrite, Delete')
        try { $read = $stream.Read($bytes, 0, 4) } finally { $stream.Dispose() }
    }
    catch {
        # It exists and could not be opened: a failure, never a skip.
        $unread.Add("${File}: $($_.Exception.Message)")
        return $false
    }
    if ($read -ge 2 -and (($bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) -or ($bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF))) { return $true }
    return ($read -eq 4 -and $bytes[0] -eq 0 -and $bytes[1] -eq 0 -and $bytes[2] -eq 0xFE -and $bytes[3] -eq 0xFF)
}

try {
    # git writes file names as UTF-8. PowerShell decodes them with the console's code page, and
    # on a stock Windows console a name with an accent comes back wrong and cannot be opened.
    try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch { }
    $utf8Console = [Console]::OutputEncoding.CodePage -eq 65001
    $fail = $false

    $top = @(Invoke-Git @('-C', $Root, 'rev-parse', '--show-toplevel')) | Select-Object -First 1
    if ($gitExit -ne 0 -or -not $top) {
        Write-Host "::error::Not inside a git checkout: '$Root'. The scan reads the files git knows about, so here it would read nothing." -ForegroundColor Red
        $unread | ForEach-Object { Write-Host "  $_" }
        exit 1
    }

    $pathspec = @($Paths) + @($Exclude | ForEach-Object { ":(exclude)$_" })

    # A scan of nothing must not report "nothing found": every path has to match at least one
    # file, and something has to be left once the leave-outs are applied.
    foreach ($p in $Paths) {
        $listed = @(Invoke-Git @('-C', $top, 'ls-files', '--cached', '--others', '--exclude-standard', '--', $p))
        if ($gitExit -ne 0 -or $listed.Count -eq 0) {
            Write-Host "::error::No files to scan under '$p'. -Paths takes paths relative to the repo root." -ForegroundColor Red
            $fail = $true
        }
    }
    # -z: names are separated by a zero byte and never quoted. --cached lists a path once per
    # stage during a merge conflict, hence the set.
    $listing = (Invoke-Git (@('-C', $top, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--') + $pathspec)) -join "`n"
    if ($gitExit -ne 0) {
        Write-Host "::error::git ls-files failed (exit $gitExit). The scan did not run." -ForegroundColor Red
        $fail = $true
    }
    $files = [System.Collections.Generic.HashSet[string]]::new(
        [string[]]@($listing -split "`0" | Where-Object { $_ }), [System.StringComparer]::Ordinal)
    if ($files.Count -eq 0 -and -not $fail) {
        Write-Host '::error::No files to scan: everything under -Paths is on the leave-out list.' -ForegroundColor Red
        $fail = $true
    }
    # Plain ASCII names read the same in every code page. Others do not, and a name that comes
    # back wrong is a file that cannot be opened.
    if (-not $utf8Console -and @($files | Where-Object { $_ -match '[^\x00-\x7F]' }).Count -gt 0) {
        $unread.Add('The console could not be switched to UTF-8, so file names with accented or non-Latin characters are not read back from git correctly.')
    }

    $patterns = @(
        'InstrumentationKey='
        'https://[a-f0-9]+@[a-z0-9]+\.ingest\.sentry\.io'
        'AKIA[0-9A-Z]{16}'
        'ASIA[0-9A-Z]{16}'
        'xoxb-[0-9]+-[0-9]+-[a-zA-Z0-9]+'
        'sk_live_[0-9a-zA-Z]+'
        'sk-ant-[0-9a-zA-Z_-]+'
        'ghp_[0-9a-zA-Z]{36,}'
        '-----BEGIN ((RSA|OPENSSH|EC|PGP) )?PRIVATE KEY-----'
    ) -join '|'

    $secrets = Search-Tree -Options '-nIE' -Pattern $patterns -Spec $pathspec
    if ($secrets.Lines.Count -gt 0) {
        Write-Host '::error::Secret-shape match:' -ForegroundColor Red
        $secrets.Lines | ForEach-Object { Write-Host "  $_" }
        $fail = $true
    }
    if ($secrets.Failed) {
        Write-Host "::error::git grep failed (exit $($secrets.Failed)). The secret scan did not finish." -ForegroundColor Red
        $fail = $true
    }

    # git grep searches bytes. In a UTF-16 or UTF-32 file every other byte is zero, so it either
    # takes the file for binary and skips it, or searches it and can never match. A config file
    # saved in the wrong encoding would never be read. Every listed file is checked for the mark,
    # whatever git made of it, and those that have one are decoded and searched.
    $wide = @($files | ForEach-Object { Join-Path $top $_ } | Where-Object { Test-WideBom $_ })
    if ($wide.Count -gt 0) {
        $wideHits = @(Select-String -LiteralPath $wide -Pattern $patterns -CaseSensitive)
        if ($wideHits.Count -gt 0) {
            Write-Host '::error::Secret-shape match (UTF-16 or UTF-32 file):' -ForegroundColor Red
            $wideHits | ForEach-Object {
                $relative = [System.IO.Path]::GetRelativePath($top, $_.Path).Replace('\', '/')
                Write-Host "  ${relative}:$($_.LineNumber):$($_.Line)"
            }
            $fail = $true
        }
    }

    # Dependency manifests anywhere in the tree. Case-insensitive on purpose: npm and PyPI names
    # are lower case (@sentry/nextjs, sentry-sdk, mixpanel, newrelic).
    $manifests = ':(glob)**/*.csproj', ':(glob)**/package.json', ':(glob)**/pyproject.toml', ':(glob)**/requirements*.txt'
    $banned = Search-Tree -Options '-lIiE' -Pattern $BannedPackages -Spec $manifests
    if ($banned.Lines.Count -gt 0) {
        $banned.Lines | ForEach-Object { Write-Host "::error::Banned package match in $_" -ForegroundColor Red }
        $fail = $true
    }
    if ($banned.Failed) {
        Write-Host "::error::git grep failed (exit $($banned.Failed)). The banned-package scan did not finish." -ForegroundColor Red
        $fail = $true
    }

    if ($unread.Count -gt 0) {
        Write-Host '::error::Not everything could be read, so this is not a pass:' -ForegroundColor Red
        $unread | ForEach-Object { Write-Host "  $_" }
        $fail = $true
    }

    if ($fail) { exit 1 }
    Write-Host "No secret-shaped strings or banned packages found ($($files.Count) files)." -ForegroundColor Green
    # Explicit: the last git grep left 1 ("no match") in $LASTEXITCODE, and a caller that reads
    # it after running this script in-process would take a clean pass for a failure.
    exit 0
}
finally {
    try { [Console]::OutputEncoding = $consoleEncoding } catch { }
    Remove-Item -LiteralPath $gitStderr -ErrorAction SilentlyContinue
}
