param(
    [Parameter(Mandatory = $true)][string]$Pptx,
    [Parameter(Mandatory = $true)][int]$Slide,
    [int]$SplitAfterParagraph,
    [switch]$DryRun
)
# Split one overflowing list slide in two with PowerPoint COM.
# The body placeholder keeps paragraphs 1..SplitAfterParagraph; a duplicate of the
# slide receives the rest, whose first paragraph must be the next group label.
# List the paragraphs first with: measure_overflow.ps1 -Pptx <deck> -Slide <n>
$ErrorActionPreference = 'Stop'

if (-not $SplitAfterParagraph -and -not $DryRun) {
    throw "pass -SplitAfterParagraph <k>, or -DryRun to only list paragraphs"
}

$pptxPath = (Resolve-Path -LiteralPath $Pptx).Path
$ppt = New-Object -ComObject PowerPoint.Application
try {
    $ppt.Visible = $true
    $ppt.DisplayAlerts = 1   # ppAlertsNone: never block on a modal prompt
    $pres = $ppt.Presentations.Open($pptxPath, $false, $false, $false)
    try {
        $source = $pres.Slides.Item($Slide)
        $body = $null
        foreach ($shape in $source.Shapes) {
            if ($shape.HasTextFrame -and $shape.TextFrame2.HasText -and $shape.TextFrame2.TextRange.Paragraphs().Count -gt 2) {
                $body = $shape
                break
            }
        }
        if ($null -eq $body) { throw "no multi-paragraph body shape on slide $Slide" }
        $paragraphs = $body.TextFrame2.TextRange.Paragraphs()
        Write-Output ("slide {0}: {1} paragraphs" -f $Slide, $paragraphs.Count)
        for ($i = 1; $i -le $paragraphs.Count; $i++) {
            $text = ($paragraphs.Item($i).Text -replace "[`r`n]+", " ")
            Write-Output ("  p{0}: {1}" -f $i, $text.Substring(0, [Math]::Min(60, $text.Length)))
        }
        if ($DryRun) { return }

        if ($paragraphs.Count -le $SplitAfterParagraph) { throw "nothing to move after p$SplitAfterParagraph" }

        $copy = $source.Duplicate()
        $copy.MoveTo($Slide + 1)
        foreach ($shape in $copy.Shapes) {
            if ($shape.HasTextFrame -and $shape.TextFrame2.HasText -and $shape.TextFrame2.TextRange.Paragraphs().Count -eq $paragraphs.Count) {
                $copyBody = $shape
                break
            }
        }
        if ($null -eq $copyBody) { throw "could not find the copied body placeholder" }

        for ($i = $SplitAfterParagraph; $i -ge 1; $i--) {
            $copyBody.TextFrame2.TextRange.Paragraphs($i).Delete()
        }
        for ($i = $paragraphs.Count; $i -gt $SplitAfterParagraph; $i--) {
            $body.TextFrame2.TextRange.Paragraphs($i).Delete()
        }
        $pres.Save()
        Write-Output ("SPLIT OK: slide {0} keeps {1} paragraphs; new slide {2} holds {3}" -f `
                $Slide, $body.TextFrame2.TextRange.Paragraphs().Count, ($Slide + 1), $copyBody.TextFrame2.TextRange.Paragraphs().Count)
    }
    finally {
        $pres.Close()
    }
}
finally {
    $ppt.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($ppt) | Out-Null
}