$ffmpeg = "C:\Users\User\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe"

# 1. BGM 處理: 轉為頂級 OGG (Quality 10)
$bgms = @("bgm.mp3", "bgm_fast.mp3")
foreach ($name in $bgms) {
    if (Test-Path $name) {
        $out = $name -replace '\.mp3$', '.ogg'
        Write-Host "--- Converting BGM $name to High-Quality OGG ---"
        & $ffmpeg -i $name -c:a libvorbis -q:a 10 $out -y
        if ($?) { Remove-Item $name }
    }
}

# 2. 短音效處理: 轉為無損 WAV
$sfxs = Get-ChildItem -Filter *.mp3 | Where-Object { $_.Name -notin $bgms }
foreach ($file in $sfxs) {
    $out = $file.Name -replace '\.mp3$', '.wav'
    Write-Host "--- Converting SFX $($file.Name) to Lossless WAV ---"
    & $ffmpeg -i $file.FullName -c:a pcm_s16le $out -y
    if ($?) { Remove-Item $file.FullName }
}

# 3. 原始 WAV 保持不變
Write-Host "Done! Original WAVs and newly converted WAVs will be preserved."
