#!/bin/sh
# Time-synced side-by-side: reference (left) | remake (right), each 540x960, labelled; audio = both mixed.
# usage: tools/side_by_side.sh REF.mp4 REMAKE.mp4 OUT.mp4
set -e
ffmpeg -y -v error -i "$1" -i "$2" -filter_complex "\
[0:v]fps=30,scale=540:960,setsar=1,drawtext=text='reference':x=20:y=20:fontsize=34:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8[a];\
[1:v]fps=30,scale=540:960,setsar=1,drawtext=text='remake':x=20:y=20:fontsize=34:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8[b];\
[a][b]hstack=inputs=2[v];[0:a][1:a]amix=inputs=2:duration=shortest:normalize=0,volume=0.7[aud]" \
 -map "[v]" -map "[aud]" -shortest -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p -c:a aac -b:a 160k -movflags +faststart "$3"
