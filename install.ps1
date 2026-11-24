# 大肥鱼推荐组合 · 一键安装
# 用法（PowerShell 里粘这一行）：
#   irm https://raw.githubusercontent.com/<作者>/dafeiyu-home/main/install.ps1 | iex
#
# 手动跑：
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun     # 只看会做什么，不真装
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Only A     # 只装 A 档
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Only B     # 只装 B 档

param(
    [switch]$DryRun,
    [ValidateSet("All", "A", "B")]
    [string]$Only = "All",
    [string]$Profile = "desktop"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ─────────── 配置
$PLUGINS = @(
    @{ name = "dsh-agent-md";                                      tier = "A"; note = "人格，用 Markdown 配" },
    @{ name = "dsh-engramory";                                     tier = "A"; note = "跨对话记忆" },
    @{ name = "dsh-memewiki";                                      tier = "A"; note = "梗百科 6792 条" },
    @{ name = "dsh-meme";                                          tier = "A"; note = "表情包 300+" },
    @{ name = "dsh-dream-skin";                                    tier = "A"; note = "配色（四件套之一）" },
    @{ name = "@smalltailqwq/dsh-client-ui-skin-maid-atelier";     tier = "A"; note = "女仆皮肤（四件套之二）" },
    @{ name = "dsh-splash-animation";                              tier = "A"; note = "启动动画（四件套之三）" },
    @{ name = "dsh-whale-widget";                                  tier = "A"; note = "余额小鲸鱼（四件套之四）" },
    @{ name = "dsh-computer-use";                                  tier = "A"; note = "操作电脑（仅 Windows）" },
    @{ name = "dsh-deep-research";                                 tier = "A"; note = "深度研究" },
    @{ name = "dsh-media-skills";                                  tier = "A"; note = "读图" },
    @{ name = "dsh-pocket";                                        tier = "A"; note = "手机扫码用" },
    @{ name = "@deepseek-ai/dsh-experimental-plugin-market-bundle"; tier = "A"; note = "插件市场" }
)

$SELF_REPO = "https://github.com/deepsleepcatm/dafeiyu-home.git"
$SELF_TAG  = "v0.1.0"

# ─────────── 工具函数
function Say($msg, $color = "White") { Write-Host $msg -ForegroundColor $color }
function Ok($msg)   { Say "  [OK] $msg" "Green" }
function Warn($msg) { Say "  [!!] $msg" "Yellow" }
function Bad($msg)  { Say "  [XX] $msg" "Red" }
function Step($msg) { Say "`n=== $msg ===" "Cyan" }

$fail = @()
$skip = @()

# ─────────── 0. 检查环境
Step "0 / 4  检查环境"

$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE ".dsh" }
if (-not (Test-Path $dshHome)) {
    Bad "找不到 DSH 主目录：$dshHome"
    Bad "先装 DSH 再跑这个脚本。"
    exit 1
}
Ok "DSH 主目录：$dshHome"

$profileDir = Join-Path $dshHome "profiles\$Profile"
if (-not (Test-Path $profileDir)) {
    Bad "找不到 profile：$profileDir"
    Bad "先启动一次 DSH（选 $Profile 这个 profile），再来跑。"
    exit 1
}
Ok "profile：$profileDir"

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Bad "找不到 node。先装 Node.js 20 以上。"
    exit 1
}
$nodeVer = (& node --version) -replace 'v', ''
Ok "Node.js $nodeVer"

$npx = Get-Command npx.cmd -ErrorAction SilentlyContinue
if (-not $npx) { $npx = Get-Command npx -ErrorAction SilentlyContinue }
if (-not $npx) {
    Bad "找不到 npx。Node.js 装完应该有，检查 PATH。"
    exit 1
}
Ok "npx 可用"

$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    $pyVer = (& python --version 2>&1)
    Ok "Python：$pyVer"
} else {
    Warn "没有 python，B 档的记忆工具跑不了（A 档不受影响）"
}

if ($DryRun) { Say "`n*** DRY RUN：下面只打印，不真装 ***" "Magenta" }

# ─────────── 1. 装 A 档插件
Step "1 / 4  装 A 档插件（13 个）"

$toInstall = $PLUGINS | Where-Object { $Only -eq "All" -or $Only -eq "A" -or $_.tier -eq $Only }
if ($Only -eq "B") {
    Say "  跳过（-Only B）" "DarkGray"
} else {
    foreach ($p in $toInstall) {
        Say "  → $($p.name)  $($p.note)" "Gray"
    }
    $pkgs = $toInstall | ForEach-Object { $_.name }
    if ($DryRun) {
        Say "  会执行： npx @deepseek-ai/dsh plugin --profile $Profile add $($pkgs -join ' ')" "Magenta"
    } else {
        Say "  装中，可能要几分钟…" "Gray"
        try {
            & npx @deepseek-ai/dsh plugin --profile $Profile add @pkgs 2>&1 | ForEach-Object { "    $_" }
            if ($LASTEXITCODE -eq 0) {
                Ok "13 个插件装完"
            } else {
                Warn "pnpm 退出码 $LASTEXITCODE，可能有个别失败。重启 DSH 后看插件列表。"
                $fail += "插件安装（退出码 $LASTEXITCODE）"
            }
        } catch {
            Bad "插件安装失败：$_"
            $fail += "插件安装"
        }
    }
}

# ─────────── 2. 拉自研部分
Step "2 / 4  拉自研部分（B 档）"

if ($Only -eq "A") {
    Say "  跳过（-Only A）" "DarkGray"
} else {
    $tmp = Join-Path $env:TEMP "dafeiyu-home-$([guid]::NewGuid().ToString('N').Substring(0,8))"
    if ($SELF_REPO -like "*deepsleepcatm*") {
        Warn "仓库地址还是占位符（deepsleepcatm），跳过。传了 GitHub 之后改成真实地址。"
        $skip += "自研部分（仓库还没上传）"
    } elseif ($DryRun) {
        Say "  会执行： git clone --depth 1 --branch $SELF_TAG $SELF_REPO $tmp" "Magenta"
        Say "  然后复制 01-soul → $dshHome\soul" "Magenta"
        Say "        复制 03-memory → $dshHome\mem-tools" "Magenta"
        Say "        复制 05-tools  → $dshHome\tools" "Magenta"
    } else {
        $git = Get-Command git -ErrorAction SilentlyContinue
        if (-not $git) {
            Bad "没有 git，拉不了自研部分。装 git 或手动下载 zip。"
            $fail += "自研部分（缺 git）"
        } else {
            Say "  从 GitHub 拉…" "Gray"
            & git clone --depth 1 --branch $SELF_TAG $SELF_REPO $tmp 2>&1 | ForEach-Object { "    $_" }
            if (Test-Path $tmp) {
                $map = @{
                    "B-自研部分/01-soul"   = (Join-Path $dshHome "soul")
                    "B-自研部分/03-memory" = (Join-Path $dshHome "mem-tools")
                    "B-自研部分/05-tools"  = (Join-Path $dshHome "tools")
                }
                foreach ($src in $map.Keys) {
                    $from = Join-Path $tmp $src
                    $to = $map[$src]
                    if (Test-Path $from) {
                        if (-not (Test-Path $to)) { New-Item -ItemType Directory -Path $to -Force | Out-Null }
                        Copy-Item "$from\*" $to -Recurse -Force
                        Ok "$src → $to"
                    }
                }
                # config
                $cfgEx = Join-Path $tmp "config.example.json"
                $cfg = Join-Path $dshHome "mem-tools\config.json"
                if ((Test-Path $cfgEx) -and -not (Test-Path $cfg)) {
                    Copy-Item $cfgEx $cfg
                    Ok "生成 config.json（记得改里面的路径）"
                }
                Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
            } else {
                Bad "clone 失败，检查网络或仓库地址"
                $fail += "自研部分（clone 失败）"
            }
        }
    }
}

# ─────────── 3. 可选：桌宠
Step "3 / 4  桌宠（可选，要自己下）"
Say "  这个不进脚本，因为 282 MB 还得你点确认：" "Gray"
Say "    https://github.com/MerZlin/dsh-pet-indesktop/releases" "Blue"
Say "    下 dsh-pet-standalone-webm-chat-setup.exe，双击装。装完占 3.4 GB。" "Gray"

# ─────────── 4. 报告
Step "4 / 4  结果"

Say "`n【已经装上的】" "Green"
if ($Only -ne "B") { Say "  A 档 13 个插件（重启 DSH 后生效）" "White" }
if ($Only -ne "A" -and $skip.Count -eq 0) { Say "  自研部分（灵魂 / 记忆工具 / 运维工具）" "White" }

if ($fail.Count -gt 0) {
    Say "`n【失败的】" "Red"
    $fail | ForEach-Object { Say "  - $_" "Red" }
}

if ($skip.Count -gt 0) {
    Say "`n【跳过的】" "Yellow"
    $skip | ForEach-Object { Say "  - $_" "Yellow" }
}

Say "`n【接下来你要做的】" "Cyan"
Say "  1. 重启 DSH（关掉再打开）" "White"
Say "  2. 问它一句「我们上次聊到什么」，能答出具体内容就是记忆通了" "White"
Say "  3. 界面看到壁纸 + 女仆角色 + 右下角小鲸鱼，样子就对了" "White"
if ($Only -ne "A") {
    Say "  4. 改 ~/.dsh/soul/charter.md（宪章不能照抄，那是别人的价值观）" "White"
}

Say "`n完了。有问题看 README。" "DarkGray"
