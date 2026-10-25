param (
    [string]$ImagePath = ""
)

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
Function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime] | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()

function Run-Ocr($targetPath) {
    if (Test-Path $targetPath) {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($targetPath)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $ocrResult = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        return $ocrResult.Text
    }
    return ""
}

if ($ImagePath -and (Test-Path $ImagePath)) {
    $res = Run-Ocr $ImagePath
    Write-Output $res
} else {
    $images = @(
        "C:\Users\Admin\Downloads\bet.png",
        "C:\Users\Admin\Downloads\bet (1).png",
        "C:\Users\Admin\Downloads\bet (2).png",
        "C:\Users\Admin\Downloads\bet (3).png"
    )
    foreach ($img in $images) {
        if (Test-Path $img) {
            Write-Output "========================================"
            Write-Output "FILE: $img"
            Write-Output (Run-Ocr $img)
        }
    }
}
