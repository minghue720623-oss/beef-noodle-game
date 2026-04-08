$ffmpeg = "C:\Users\User\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe"

Get-ChildItem -Filter *.mp3 | ForEach-Object {
    $out = $_.Name -replace '\.mp3$', '.ogg'
    Write-Host "Converting $_ to $out"
    & $ffmpeg -i $_.FullName -c:a libvorbis -q:a 9 $out -y
    if ($?) { Remove-Item $_.FullName }
}

Get-ChildItem -Filter *.wav | ForEach-Object {
    $out = $_.Name -replace '\.wav$', '.ogg'
    Write-Host "Converting $_ to $out"
    & $ffmpeg -i $_.FullName -c:a libvorbis -q:a 9 $out -y
    if ($?) { Remove-Item $_.FullName }
}
