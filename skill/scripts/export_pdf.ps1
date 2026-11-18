param(
    [Parameter(Mandatory = $true)][string]$Pptx,
    [Parameter(Mandatory = $true)][string]$Pdf
)
# Export a PPTX to PDF with PowerPoint COM (Windows + PowerPoint).
# Optional provider in operation-guide §6b: officecli PDF plugin, then LibreOffice,
# then this script. Exits 1 when PowerPoint cannot be started.
$ErrorActionPreference = 'Stop'

$pptxPath = (Resolve-Path -LiteralPath $Pptx).Path
$pdfPath = [System.IO.Path]::GetFullPath($Pdf)

try {
    $ppt = New-Object -ComObject PowerPoint.Application
} catch {
    Write-Error "PowerPoint COM is unavailable: $($_.Exception.Message)"
    exit 1
}

$slides = 0
try {
    $ppt.Visible = $true
    $ppt.DisplayAlerts = 1   # ppAlertsNone: no modal prompts while saving
    $pres = $ppt.Presentations.Open($pptxPath, $true, $false, $false)
    try {
        $slides = $pres.Slides.Count
        $pres.SaveAs($pdfPath, 32)   # 32 = ppSaveAsPDF
    }
    finally {
        $pres.Close()
    }
}
finally {
    $ppt.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($ppt) | Out-Null
}

Write-Output "PDF: $pdfPath"
Write-Output ("PAGES: {0}" -f $slides)
Write-Output ("BYTES: {0}" -f (Get-Item -LiteralPath $pdfPath).Length)