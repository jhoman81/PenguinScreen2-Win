#include <cstdio>
#include <cstdlib>
void AbortWithMessage(const char* m){std::fprintf(stderr,"abort: %s\n",m);std::abort();}
