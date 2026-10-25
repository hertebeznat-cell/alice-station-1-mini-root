/* ALSA PCM helper for native plugins. MIT, Alice Homebrew contributors.
 * Build: aarch64-linux-gnu-gcc -Os pcm.c -o alice-pcm -static $(pkg-config --static --cflags --libs alsa) -lm -ldl -lpthread
 * Raw interleaved PCM: capture writes stdout, playback reads stdin.
 * No recording starts until an author explicitly invokes `capture`.
 */
#include <alsa/asoundlib.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static volatile sig_atomic_t stopped=0;
static void stop(int s){(void)s;stopped=1;}
int main(int argc,char **argv){
 if(argc!=7 || (strcmp(argv[1],"capture") && strcmp(argv[1],"playback"))){fprintf(stderr,"Usage: alice-pcm capture|playback DEVICE RATE CHANNELS S16_LE|S32_LE FRAMES\n");return 2;}
 int capture=!strcmp(argv[1],"capture"),rate=atoi(argv[3]),channels=atoi(argv[4]),frames=atoi(argv[6]);
 snd_pcm_format_t format=snd_pcm_format_value(argv[5]);
 if(rate<8000||rate>192000||channels<1||channels>8||frames<64||frames>8192||(format!=SND_PCM_FORMAT_S16_LE&&format!=SND_PCM_FORMAT_S32_LE))return 2;
 snd_pcm_t *pcm=NULL;int err=snd_pcm_open(&pcm,argv[2],capture?SND_PCM_STREAM_CAPTURE:SND_PCM_STREAM_PLAYBACK,0);
 if(err<0){fprintf(stderr,"open: %s\n",snd_strerror(err));return 1;}
 err=snd_pcm_set_params(pcm,format,SND_PCM_ACCESS_RW_INTERLEAVED,channels,rate,1,100000);
 if(err<0){fprintf(stderr,"params: %s\n",snd_strerror(err));snd_pcm_close(pcm);return 1;}
 size_t frame_bytes=channels*snd_pcm_format_physical_width(format)/8,bytes=frames*frame_bytes;
 char *buffer=malloc(bytes);if(!buffer){snd_pcm_close(pcm);return 1;}
 signal(SIGTERM,stop);signal(SIGINT,stop);
 while(!stopped){
  snd_pcm_sframes_t n;
  if(capture){n=snd_pcm_readi(pcm,buffer,frames);if(n>0 && fwrite(buffer,frame_bytes,n,stdout)!=(size_t)n)break;}
  else {size_t count=fread(buffer,frame_bytes,frames,stdin);if(!count)break;size_t done=0;n=0;while(done<count && !stopped){n=snd_pcm_writei(pcm,buffer+done*frame_bytes,count-done);if(n<0){n=snd_pcm_recover(pcm,n,1);if(n<0)break;}else done+=n;}}
  if(n<0){err=snd_pcm_recover(pcm,n,1);if(err<0){fprintf(stderr,"pcm: %s\n",snd_strerror(err));break;}}
 }
 snd_pcm_drop(pcm);snd_pcm_close(pcm);free(buffer);return err<0?1:0;
}
