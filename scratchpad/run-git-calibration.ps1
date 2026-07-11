[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$GitExe = (Get-Command git).Source
$PowerShellExe = (Get-Command powershell).Source
$TaskkillExe = (Get-Command taskkill).Source
$Root = Join-Path $env:TEMP ("coagentia-m6-git-校准-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$Repo = Join-Path $Root "主仓库"
$Cases = New-Object System.Collections.Generic.List[object]
$LockProcess = $null

function ConvertTo-NativeArgument {
    param([string]$Value)

    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Stop-ProcessTree {
    param([int]$ProcessId)

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $TaskkillExe
    $psi.Arguments = "/F /T /PID $ProcessId"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $Utf8
    $psi.StandardErrorEncoding = $Utf8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    [pscustomobject]@{
        exit_code = $process.ExitCode
        stdout = $stdoutTask.Result.TrimEnd()
        stderr = $stderrTask.Result.TrimEnd()
    }
}

function Invoke-ProcessUtf8 {
    param(
        [string]$FileName,
        [string[]]$Arguments,
        [int]$TimeoutSeconds = 30
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FileName
    $psi.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join " ")
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $Utf8
    $psi.StandardErrorEncoding = $Utf8
    $psi.EnvironmentVariables["GIT_TERMINAL_PROMPT"] = "0"

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $process.StandardInput.Close()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $completed = $process.WaitForExit($TimeoutSeconds * 1000)
    if (-not $completed) {
        $kill = Stop-ProcessTree -ProcessId $process.Id
        $process.WaitForExit()
    }
    [pscustomobject]@{
        exit_code = if ($completed) { $process.ExitCode } else { -1 }
        timed_out = -not $completed
        stdout = $stdoutTask.Result.TrimEnd()
        stderr = $stderrTask.Result.TrimEnd()
    }
}

function Invoke-Git {
    param(
        [string]$WorkingDirectory,
        [string[]]$Arguments,
        [int]$TimeoutSeconds = 30
    )

    Invoke-ProcessUtf8 -FileName $GitExe -Arguments (@("-C", $WorkingDirectory) + $Arguments) -TimeoutSeconds $TimeoutSeconds
}

function Assert-GitSuccess {
    param([object]$Result, [string]$Operation)

    if ($Result.exit_code -ne 0) {
        throw "$Operation failed: $($Result.stderr)"
    }
}

function Add-Case {
    param(
        [string]$Name,
        [bool]$Passed,
        [hashtable]$Evidence
    )

    $Cases.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        evidence = [pscustomobject]$Evidence
    })
}

function Write-Utf8File {
    param([string]$Path, [string]$Content)

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    [System.IO.File]::WriteAllText($Path, $Content, $Utf8)
}

function Remove-CalibrationTree {
    param([string]$Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    $tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd("\") + "\"
    if (-not $fullPath.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([IO.Path]::GetFileName($fullPath)).StartsWith("coagentia-m6-git-", [StringComparison]::Ordinal)) {
        throw "unsafe calibration cleanup target: $fullPath"
    }
    $extendedPath = "\\?\$fullPath"
    $entries = @([IO.Directory]::EnumerateFileSystemEntries($extendedPath, "*", [IO.SearchOption]::AllDirectories))
    foreach ($entry in $entries) {
        try {
            [IO.File]::SetAttributes($entry, [IO.FileAttributes]::Normal)
        }
        catch {
            # A concurrently removed entry is already clean.
        }
    }
    [IO.File]::SetAttributes($extendedPath, [IO.FileAttributes]::Normal)
    [IO.Directory]::Delete($extendedPath, $true)
}

function Start-ExclusiveFileHolder {
    param([string]$FilePath, [string]$ReadyPath)

    $escapedFile = $FilePath.Replace("'", "''")
    $escapedReady = $ReadyPath.Replace("'", "''")
    $code = @"
`$stream = [System.IO.File]::Open('$escapedFile', [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
[System.IO.File]::WriteAllText('$escapedReady', 'ready', (New-Object System.Text.UTF8Encoding(`$false)))
Start-Sleep -Seconds 120
`$stream.Dispose()
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PowerShellExe
    $psi.Arguments = "-NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $Utf8
    $psi.StandardErrorEncoding = $Utf8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    return $process
}

try {
    [void](New-Item -ItemType Directory -Path $Root -Force)
    $gitVersion = (Invoke-ProcessUtf8 -FileName $GitExe -Arguments @("--version")).stdout
    $globalLongPaths = (Invoke-ProcessUtf8 -FileName $GitExe -Arguments @("config", "--show-origin", "--get", "core.longpaths"))
    $globalAutoCrlf = (Invoke-ProcessUtf8 -FileName $GitExe -Arguments @("config", "--show-origin", "--get", "core.autocrlf"))

    $init = Invoke-ProcessUtf8 -FileName $GitExe -Arguments @("init", "--initial-branch=main", $Repo)
    Assert-GitSuccess $init "git init"
    Assert-GitSuccess (Invoke-Git $Repo @("config", "user.name", "CoAgentia Calibration")) "config user.name"
    Assert-GitSuccess (Invoke-Git $Repo @("config", "user.email", "calibration@coagentia.invalid")) "config user.email"
    Assert-GitSuccess (Invoke-Git $Repo @("config", "core.autocrlf", "false")) "config autocrlf"

    Write-Utf8File (Join-Path $Repo "README.md") "seed`n"
    Write-Utf8File (Join-Path $Repo "中文路径.txt") "初始`n"
    Assert-GitSuccess (Invoke-Git $Repo @("add", "--all")) "seed add"
    Assert-GitSuccess (Invoke-Git $Repo @("commit", "-m", "初始提交：中文路径")) "seed commit"

    $nativeTree = Join-Path $Root "worktrees\反斜杠树"
    $nativeAdd = Invoke-Git $Repo @("worktree", "add", "-b", "calib/native", $nativeTree, "HEAD")
    $nativeList = Invoke-Git $Repo @("worktree", "list", "--porcelain")
    $nativeRemove = Invoke-Git $Repo @("worktree", "remove", $nativeTree)
    Add-Case "worktree-native-separators" (($nativeAdd.exit_code -eq 0) -and ($nativeRemove.exit_code -eq 0)) @{
        target = $nativeTree
        add_stdout = $nativeAdd.stdout
        add_stderr = $nativeAdd.stderr
        porcelain = $nativeList.stdout
        remove_stderr = $nativeRemove.stderr
    }

    $forwardTreeNative = Join-Path $Root "worktrees\正斜杠树"
    $forwardTree = $forwardTreeNative.Replace("\", "/")
    $forwardAdd = Invoke-Git $Repo @("worktree", "add", "-b", "calib/forward", $forwardTree, "HEAD")
    $forwardList = Invoke-Git $Repo @("worktree", "list", "--porcelain")
    $forwardRemove = Invoke-Git $Repo @("worktree", "remove", $forwardTree)
    Add-Case "worktree-forward-separators" (($forwardAdd.exit_code -eq 0) -and ($forwardRemove.exit_code -eq 0)) @{
        target = $forwardTree
        add_stdout = $forwardAdd.stdout
        add_stderr = $forwardAdd.stderr
        porcelain = $forwardList.stdout
        remove_stderr = $forwardRemove.stderr
    }

    $lockTree = Join-Path $Root "worktrees\锁定树"
    Assert-GitSuccess (Invoke-Git $Repo @("worktree", "add", "-b", "calib/locked", $lockTree, "HEAD")) "locked worktree add"
    Assert-GitSuccess (Invoke-Git $Repo @("worktree", "lock", "--reason", "M6 calibration", $lockTree)) "worktree lock"
    $lockedRemove = Invoke-Git $Repo @("worktree", "remove", $lockTree)
    $lockedFile = Get-ChildItem -LiteralPath (Join-Path $Repo ".git\worktrees") -Filter locked -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    Assert-GitSuccess (Invoke-Git $Repo @("worktree", "unlock", $lockTree)) "worktree unlock"
    $unlockedRemove = Invoke-Git $Repo @("worktree", "remove", $lockTree)
    Add-Case "worktree-administrative-lock" (($lockedRemove.exit_code -ne 0) -and ($unlockedRemove.exit_code -eq 0)) @{
        locked_remove_exit = $lockedRemove.exit_code
        locked_remove_stderr = $lockedRemove.stderr
        locked_marker = if ($lockedFile) { $lockedFile.FullName } else { $null }
        unlocked_remove_exit = $unlockedRemove.exit_code
    }

    Write-Utf8File (Join-Path $Repo "index-lock-probe.txt") "probe`n"
    Write-Utf8File (Join-Path $Repo ".git\index.lock") "calibration lock"
    $indexLockedAdd = Invoke-Git $Repo @("add", "index-lock-probe.txt")
    Remove-Item -LiteralPath (Join-Path $Repo ".git\index.lock") -Force
    $indexUnlockedAdd = Invoke-Git $Repo @("add", "index-lock-probe.txt")
    Assert-GitSuccess $indexUnlockedAdd "index unlocked add"
    Assert-GitSuccess (Invoke-Git $Repo @("reset", "--", "index-lock-probe.txt")) "index probe reset"
    Remove-Item -LiteralPath (Join-Path $Repo "index-lock-probe.txt") -Force
    Add-Case "index-lock-file" (($indexLockedAdd.exit_code -ne 0) -and ($indexUnlockedAdd.exit_code -eq 0)) @{
        locked_exit = $indexLockedAdd.exit_code
        locked_stderr = $indexLockedAdd.stderr
        unlocked_exit = $indexUnlockedAdd.exit_code
    }

    $occupiedTree = Join-Path $Root "worktrees\进程占用树"
    Assert-GitSuccess (Invoke-Git $Repo @("worktree", "add", "-b", "calib/occupied", $occupiedTree, "HEAD")) "occupied worktree add"
    $heldFile = Join-Path $occupiedTree "held.txt"
    $readyFile = Join-Path $Root "holder-ready.txt"
    Write-Utf8File $heldFile "held`n"
    $LockProcess = Start-ExclusiveFileHolder -FilePath $heldFile -ReadyPath $readyFile
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $readyFile)) {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "exclusive file holder did not become ready"
        }
        Start-Sleep -Milliseconds 100
    }
    $occupiedRemove = Invoke-Git $Repo @("worktree", "remove", "--force", $occupiedTree) 10
    $listedAfterFailure = (Invoke-Git $Repo @("worktree", "list", "--porcelain")).stdout
    $pathAfterFailure = Test-Path -LiteralPath $occupiedTree
    $killResult = Stop-ProcessTree -ProcessId $LockProcess.Id
    $LockProcess.WaitForExit()
    $LockProcess = $null
    if ($listedAfterFailure -match [regex]::Escape($occupiedTree.Replace("\", "/"))) {
        $occupiedRetry = Invoke-Git $Repo @("worktree", "remove", "--force", $occupiedTree)
    } else {
        $occupiedRetry = Invoke-Git $Repo @("worktree", "prune")
        if (Test-Path -LiteralPath $occupiedTree) {
            Remove-Item -LiteralPath $occupiedTree -Recurse -Force
        }
    }
    Add-Case "worktree-process-occupation" (($occupiedRemove.exit_code -ne 0) -and ($occupiedRetry.exit_code -eq 0)) @{
        first_remove_exit = $occupiedRemove.exit_code
        first_remove_timed_out = $occupiedRemove.timed_out
        first_remove_stdout = $occupiedRemove.stdout
        first_remove_stderr = $occupiedRemove.stderr
        still_listed_after_failure = ($listedAfterFailure -match "calib/occupied")
        path_exists_after_failure = $pathAfterFailure
        taskkill_exit = $killResult.exit_code
        retry_exit = $occupiedRetry.exit_code
    }

    $longPathMatrix = New-Object System.Collections.Generic.List[object]
    foreach ($targetLength in @(140, 170, 190, 210, 230, 250, 270, 300)) {
        $matrixTree = Join-Path $Root "long-path-matrix"
        $segmentIndex = 0
        while ($matrixTree.Length -lt ($targetLength - 14)) {
            $segmentIndex += 1
            $matrixTree = Join-Path $matrixTree ("s$segmentIndex-" + ("x" * 17))
        }
        $matrixTree = Join-Path $matrixTree ("tree-$targetLength")
        $matrixAdd = Invoke-Git $Repo @("-c", "core.longpaths=true", "worktree", "add", "-b", "calib/long-$targetLength", $matrixTree, "HEAD")
        $matrixRemove = $null
        if ($matrixAdd.exit_code -eq 0) {
            $matrixRemove = Invoke-Git $Repo @("-c", "core.longpaths=true", "worktree", "remove", $matrixTree)
        }
        $longPathMatrix.Add([pscustomobject]@{
            requested_length = $targetLength
            actual_length = $matrixTree.Length
            add_exit = $matrixAdd.exit_code
            add_stderr = $matrixAdd.stderr
            remove_exit = if ($matrixRemove) { $matrixRemove.exit_code } else { $null }
        })
    }
    $matrixSuccesses = @($longPathMatrix | Where-Object { $_.add_exit -eq 0 -and $_.remove_exit -eq 0 })
    $matrixFailures = @($longPathMatrix | Where-Object { $_.add_exit -ne 0 })
    Add-Case "worktree-long-path" (($matrixSuccesses.Count -gt 0) -and ($matrixFailures.Count -gt 0)) @{
        matrix = $longPathMatrix
        longest_success = if ($matrixSuccesses.Count -gt 0) { ($matrixSuccesses | Measure-Object -Property actual_length -Maximum).Maximum } else { $null }
        shortest_failure = if ($matrixFailures.Count -gt 0) { ($matrixFailures | Measure-Object -Property actual_length -Minimum).Minimum } else { $null }
    }

    $mergeTree = Join-Path $Root "worktrees\merge-success"
    Assert-GitSuccess (Invoke-Git $Repo @("worktree", "add", "-b", "calib/merge-success", $mergeTree, "HEAD")) "merge worktree add"
    Write-Utf8File (Join-Path $mergeTree "feature.txt") "feature`n"
    Assert-GitSuccess (Invoke-Git $mergeTree @("add", "feature.txt")) "merge feature add"
    Assert-GitSuccess (Invoke-Git $mergeTree @("commit", "-m", "task feature commit")) "merge feature commit"
    $beforeMerge = (Invoke-Git $Repo @("rev-parse", "HEAD")).stdout
    $mergeResult = Invoke-Git $Repo @("merge", "--no-ff", "calib/merge-success", "-m", "Merge task calibration")
    $mergeHead = (Invoke-Git $Repo @("rev-parse", "HEAD")).stdout
    $parentLine = (Invoke-Git $Repo @("rev-list", "--parents", "-n", "1", "HEAD")).stdout
    $parentCount = ($parentLine -split "\s+").Count - 1
    $mergeSubject = (Invoke-Git $Repo @("show", "-s", "--format=%s", "HEAD")).stdout
    Assert-GitSuccess (Invoke-Git $Repo @("worktree", "remove", $mergeTree)) "merge worktree remove"
    Add-Case "merge-no-ff" (($mergeResult.exit_code -eq 0) -and ($parentCount -eq 2) -and ($mergeHead -ne $beforeMerge)) @{
        before_head = $beforeMerge
        merge_head = $mergeHead
        parent_count = $parentCount
        subject = $mergeSubject
        stdout = $mergeResult.stdout
        stderr = $mergeResult.stderr
    }

    Write-Utf8File (Join-Path $Repo "conflict.txt") "base`n"
    Assert-GitSuccess (Invoke-Git $Repo @("add", "conflict.txt")) "conflict base add"
    Assert-GitSuccess (Invoke-Git $Repo @("commit", "-m", "conflict base")) "conflict base commit"
    $conflictTree = Join-Path $Root "worktrees\conflict"
    Assert-GitSuccess (Invoke-Git $Repo @("worktree", "add", "-b", "calib/conflict", $conflictTree, "HEAD")) "conflict worktree add"
    Write-Utf8File (Join-Path $conflictTree "conflict.txt") "feature side`n"
    Assert-GitSuccess (Invoke-Git $conflictTree @("add", "conflict.txt")) "conflict feature add"
    Assert-GitSuccess (Invoke-Git $conflictTree @("commit", "-m", "feature conflict")) "conflict feature commit"
    Write-Utf8File (Join-Path $Repo "conflict.txt") "main side`n"
    Assert-GitSuccess (Invoke-Git $Repo @("add", "conflict.txt")) "conflict main add"
    Assert-GitSuccess (Invoke-Git $Repo @("commit", "-m", "main conflict")) "conflict main commit"
    $preConflictHead = (Invoke-Git $Repo @("rev-parse", "HEAD")).stdout
    $conflictMerge = Invoke-Git $Repo @("merge", "--no-ff", "calib/conflict", "-m", "conflict calibration")
    $conflictFiles = (Invoke-Git $Repo @("diff", "--name-only", "--diff-filter=U")).stdout
    $mergeHeadExists = Test-Path -LiteralPath (Join-Path $Repo ".git\MERGE_HEAD")
    $abortResult = Invoke-Git $Repo @("merge", "--abort")
    $afterAbortHead = (Invoke-Git $Repo @("rev-parse", "HEAD")).stdout
    $afterAbortStatus = (Invoke-Git $Repo @("status", "--porcelain")).stdout
    $afterAbortContent = [IO.File]::ReadAllText((Join-Path $Repo "conflict.txt"), $Utf8).Trim()
    $mergeHeadAfterAbort = Test-Path -LiteralPath (Join-Path $Repo ".git\MERGE_HEAD")
    Assert-GitSuccess (Invoke-Git $Repo @("worktree", "remove", $conflictTree)) "conflict worktree remove"
    Add-Case "merge-conflict-abort" (($conflictMerge.exit_code -ne 0) -and $mergeHeadExists -and ($abortResult.exit_code -eq 0) -and ($afterAbortHead -eq $preConflictHead) -and (-not $mergeHeadAfterAbort) -and ($afterAbortStatus -eq "")) @{
        merge_exit = $conflictMerge.exit_code
        merge_stdout = $conflictMerge.stdout
        merge_stderr = $conflictMerge.stderr
        conflict_files = $conflictFiles
        merge_head_existed = $mergeHeadExists
        abort_exit = $abortResult.exit_code
        head_restored = ($afterAbortHead -eq $preConflictHead)
        status_after_abort = $afterAbortStatus
        content_after_abort = $afterAbortContent
        merge_head_after_abort = $mergeHeadAfterAbort
    }

    Write-Utf8File (Join-Path $Repo "modify.txt") "one`ntwo`nthree`n"
    Write-Utf8File (Join-Path $Repo "delete.txt") "delete me`n"
    Write-Utf8File (Join-Path $Repo "rename-old.txt") "rename payload`n"
    Write-Utf8File (Join-Path $Repo "中文 文件.txt") "中文旧内容`n"
    [IO.File]::WriteAllBytes((Join-Path $Repo "binary.bin"), [byte[]](0, 1, 2, 3, 0, 255))
    [IO.File]::WriteAllText((Join-Path $Repo "crlf.txt"), "a`r`nb`r`nc`r`n", $Utf8)
    Assert-GitSuccess (Invoke-Git $Repo @("add", "--all")) "diff base add"
    Assert-GitSuccess (Invoke-Git $Repo @("commit", "-m", "diff fixture base")) "diff base commit"
    $diffBase = (Invoke-Git $Repo @("rev-parse", "HEAD")).stdout
    $diffTree = Join-Path $Root "worktrees\diff-中文"
    Assert-GitSuccess (Invoke-Git $Repo @("worktree", "add", "-b", "calib/diff", $diffTree, "HEAD")) "diff worktree add"
    Write-Utf8File (Join-Path $diffTree "modify.txt") "one`nTWO changed`nthree`nfour`n"
    Remove-Item -LiteralPath (Join-Path $diffTree "delete.txt")
    Move-Item -LiteralPath (Join-Path $diffTree "rename-old.txt") -Destination (Join-Path $diffTree "rename-new.txt")
    Write-Utf8File (Join-Path $diffTree "中文 文件.txt") "中文新内容`n新增一行`n"
    Write-Utf8File (Join-Path $diffTree "added.txt") "added`n"
    [IO.File]::WriteAllBytes((Join-Path $diffTree "binary.bin"), [byte[]](0, 9, 8, 7, 0, 254))
    [IO.File]::WriteAllText((Join-Path $diffTree "crlf.txt"), "a`r`nB changed`r`nc`r`n", $Utf8)
    Assert-GitSuccess (Invoke-Git $diffTree @("add", "--all")) "diff changes add"
    Assert-GitSuccess (Invoke-Git $diffTree @("commit", "-m", "差异提交：增删改重命名与二进制")) "diff changes commit"
    $diffHead = (Invoke-Git $diffTree @("rev-parse", "HEAD")).stdout
    $numstatQuoted = Invoke-Git $diffTree @("-c", "core.quotepath=true", "diff", "--numstat", "--find-renames", "$diffBase..$diffHead")
    $numstatUtf8 = Invoke-Git $diffTree @("-c", "core.quotepath=false", "diff", "--numstat", "--find-renames", "$diffBase..$diffHead")
    $numstatNul = Invoke-Git $diffTree @("-c", "core.quotepath=false", "diff", "--numstat", "--find-renames", "-z", "$diffBase..$diffHead")
    $nameStatus = Invoke-Git $diffTree @("-c", "core.quotepath=false", "diff", "--name-status", "--find-renames", "$diffBase..$diffHead")
    $nameStatusNul = Invoke-Git $diffTree @("-c", "core.quotepath=false", "diff", "--name-status", "--find-renames", "-z", "$diffBase..$diffHead")
    $patch = Invoke-Git $diffTree @("-c", "core.quotepath=false", "diff", "--no-color", "--find-renames", "-p", "$diffBase..$diffHead")
    $logUtf8 = Invoke-Git $diffTree @("log", "-1", "--format=%s", $diffHead)
    $defaultDecodedSimulation = [Text.Encoding]::Default.GetString([Text.Encoding]::UTF8.GetBytes($logUtf8.stdout))
    $binaryNumstatSeen = $numstatUtf8.stdout -match '(?m)^-\s+-\s+binary\.bin$'
    $binaryPatchMarkerSeen = $patch.stdout -match 'Binary files .*binary\.bin.* differ'
    $renameSeen = $nameStatus.stdout -match '(?m)^R\d+\s+rename-old\.txt\s+rename-new\.txt$'
    $chineseReadable = $numstatUtf8.stdout.Contains("中文 文件.txt") -and $logUtf8.stdout.Contains("差异提交")
    $quotedChineseEscaped = $numstatQuoted.stdout -match '\\[0-7]{3}'
    $crlfOnlyTargetLine = (($patch.stdout -split "`n") | Where-Object { $_ -match '^[+-](?![+-])' -and $_ -match 'changed' }).Count -ge 2
    $nulDelimited = $numstatNul.stdout.Contains([char]0) -and $nameStatusNul.stdout.Contains([char]0)
    Assert-GitSuccess (Invoke-Git $Repo @("worktree", "remove", $diffTree)) "diff worktree remove"
    Add-Case "diff-numstat-patch-encoding-crlf" ($binaryNumstatSeen -and $binaryPatchMarkerSeen -and $renameSeen -and $chineseReadable -and $quotedChineseEscaped -and $crlfOnlyTargetLine -and $nulDelimited) @{
        base = $diffBase
        head = $diffHead
        numstat_core_quotepath_true = $numstatQuoted.stdout
        numstat_core_quotepath_false = $numstatUtf8.stdout
        numstat_nul_visible = $numstatNul.stdout.Replace([string][char]0, "<NUL>")
        name_status = $nameStatus.stdout
        name_status_nul_visible = $nameStatusNul.stdout.Replace([string][char]0, "<NUL>")
        patch = $patch.stdout
        binary_numstat_dash = $binaryNumstatSeen
        binary_patch_marker = $binaryPatchMarkerSeen
        rename_detected = $renameSeen
        chinese_utf8_readable = $chineseReadable
        quoted_chinese_escaped = $quotedChineseEscaped
        utf8_decoded_subject = $logUtf8.stdout
        default_gb2312_simulation = $defaultDecodedSimulation
        crlf_changed_lines_detected = $crlfOnlyTargetLine
        nul_delimited = $nulDelimited
    }

    $status = Invoke-Git $Repo @("status", "--porcelain")
    Add-Case "main-repo-clean-after-calibration" ($status.stdout -eq "") @{
        status = $status.stdout
    }

    [pscustomobject]@{
        generated_at = [DateTime]::UtcNow.ToString("o")
        platform = [Environment]::OSVersion.VersionString
        powershell = $PSVersionTable.PSVersion.ToString()
        console_output_encoding = [Console]::OutputEncoding.WebName
        console_input_encoding = [Console]::InputEncoding.WebName
        git = $gitVersion
        temp_root = $Root
        temp_root_contains_cjk = $Root.Contains("校准")
        global_core_longpaths = [pscustomobject]@{ exit_code = $globalLongPaths.exit_code; stdout = $globalLongPaths.stdout; stderr = $globalLongPaths.stderr }
        global_core_autocrlf = [pscustomobject]@{ exit_code = $globalAutoCrlf.exit_code; stdout = $globalAutoCrlf.stdout; stderr = $globalAutoCrlf.stderr }
        cases = $Cases
        passed = (($Cases | Where-Object { -not $_.passed }).Count -eq 0)
    } | ConvertTo-Json -Depth 8
}
finally {
    if ($LockProcess -and -not $LockProcess.HasExited) {
        Stop-ProcessTree -ProcessId $LockProcess.Id | Out-Null
        $LockProcess.WaitForExit()
    }
    if (Test-Path -LiteralPath $Root) {
        try {
            Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction Stop
        }
        catch {
            Remove-CalibrationTree -Path $Root
        }
        if (Test-Path -LiteralPath $Root) {
            throw "calibration cleanup left temp root: $Root"
        }
    }
}
