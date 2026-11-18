param(
    [Parameter(Mandatory = $true)][string]$Pptx,
    [double]$TolerancePt = 0.5,
    [int]$Slide = 0
)
# Measure real text overflow with PowerPoint's own layout engine (Windows + PowerPoint).
# Advisory: prints one row per overflowing shape, then OVERFLOW_SHAPES=<n>.
# -Slide N lists that slide's body paragraphs with indices (for split_overflow_page.ps1).
$ErrorActionPreference = 'Stop'

function Skip([string]$reason) {
    Write-Output "OVERFLOW SKIPPED: $reason"
    Write-Output "OVERFLOW_SHAPES=0"
    exit 0
}

$pptxPath = (Resolve-Path -LiteralPath $Pptx).Path
try {
    $ppt = New-Object -ComObject PowerPoint.Application
} catch {
    Skip "PowerPoint COM is unavailable ($($_.Exception.Message))"
}

$rows = @()
try {
    $ppt.Visible = $true
    $pres = $ppt.Presentations.Open($pptxPath, $true, $false, $false)
    try {
        if ($Slide -gt 0) {
            $target = $pres.Slides.Item($Slide)
            foreach ($shape in $target.Shapes) {
                if (-not $shape.HasTextFrame) { continue }
                $tf = $shape.TextFrame2
                if (-not $tf.HasText) { continue }
                $paras = $tf.TextRange.Paragraphs()
                Write-Output ("slide {0}: {1} paragraphs in shape '{2}'" -f $Slide, $paras.Count, $shape.Name)
                for ($i = 1; $i -le $paras.Count; $i++) {
                    $text = ($paras.Item($i).Text -replace "[`r`n]+", " ")
                    Write-Output ("  p{0}: {1}" -f $i, $text.Substring(0, [Math]::Min(60, $text.Length)))
                }
            }
            exit 0
        }
        for ($i = 1; $i -le $pres.Slides.Count; $i++) {
            $slideObj = $pres.Slides.Item($i)
            foreach ($shape in $slideObj.Shapes) {
                if (-not $shape.HasTextFrame) { continue }
                $tf = $shape.TextFrame2
                if (-not $tf.HasText) { continue }
                $available = $shape.Height - $tf.MarginTop - $tf.MarginBottom
                $needed = $tf.TextRange.BoundHeight
                $over = [Math]::Round($needed - $available, 1)
                if ($over -gt $TolerancePt) {
                    $first = ($tf.TextRange.Text -split "`r")[0]
                    $rows += [pscustomobject]@{
                        Slide   = $i
                        OverPt  = $over
                        NeedPt  = [Math]::Round($needed, 1)
                        AvailPt = [Math]::Round($available, 1)
                        Head    = $first.Substring(0, [Math]::Min(40, $first.Length))
                    }
                }
            }
        }
    }
    finally {
        $pres.Close()
    }
}
finally {
    $ppt.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($ppt) | Out-Null
}

$rows | Sort-Object -Property OverPt -Descending | Format-Table -AutoSize
Write-Output ("OVERFLOW_SHAPES={0}" -f $rows.Count)