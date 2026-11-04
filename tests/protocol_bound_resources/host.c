#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#define LOCAL static __attribute__((unused))

typedef struct {
    uint64_t revision, observed, value, published;
    unsigned bits, effects;
    bool owned, wake, result;
} BoundCurrentContext;
LOCAL bool bound_identity(BoundCurrentContext *c){return c->revision!=UINT64_MAX;}
LOCAL bool bound_current(BoundCurrentContext *c){return c->observed==c->revision;}
LOCAL bool bound_result_ready(BoundCurrentContext *c){return c->result;}
LOCAL bool bound_wake_registered(BoundCurrentContext *c){return c->wake;}
LOCAL bool bound_job_owned(BoundCurrentContext *c){return c->owned;}
LOCAL bool bound_observe(BoundCurrentContext *c){c->observed=c->revision;return(c->bits&1u)!=0;}
LOCAL bool bound_mutate(BoundCurrentContext *c){++c->revision;c->value+=7;++c->effects;return(c->bits&2u)!=0;}
LOCAL bool bound_refresh(BoundCurrentContext *c){if(!(c->bits&4u))return false;c->observed=c->revision;return true;}
LOCAL bool bound_publish(BoundCurrentContext *c){if(!bound_current(c))abort();c->published=c->value;c->result=true;++c->effects;return(c->bits&8u)!=0;}
LOCAL bool bound_park(BoundCurrentContext *c){c->wake=(c->bits&16u)!=0;return c->wake;}
LOCAL bool bound_claim(BoundCurrentContext *c){if(c->owned)return false;c->owned=true;return true;}
LOCAL void bound_discharge(BoundCurrentContext *c){if(!c->owned)abort();c->owned=false;}
LOCAL void bound_transfer(BoundCurrentContext *c){if(!c->owned||!c->wake)abort();c->owned=false;}

typedef struct {bool owned,identity;} BoundClaimContext;
LOCAL bool claim_identity(BoundClaimContext *c){return c->identity;}
LOCAL bool claim_job_owned(BoundClaimContext *c){return c->owned;}
LOCAL bool claim_job(BoundClaimContext *c){if(c->owned)return false;c->owned=true;return true;}
LOCAL void discharge_job(BoundClaimContext *c){if(!c->owned)abort();c->owned=false;}
LOCAL void transfer_job(BoundClaimContext *c){(void)c;abort();}
#define bound_current_run_CHECKED CHECKED
#define bound_claim_run_CHECKED CHECKED
#define bound_resume_run_CHECKED CHECKED
#include "bound.h"

int main(void)
{
 for(unsigned bits=0;bits<32;++bits){
  BoundCurrentContext c={.revision=3,.observed=3,.value=11,.bits=bits,.owned=true};
  bound_current_run_outcome result=bound_current_run(&c);
  if(c.owned)return 1;
  bound_current_run_outcome expected=!(bits&1)?bound_current_run_raised:
    !(bits&2)?bound_current_run_raised:!(bits&4)?bound_current_run_engine_failure:
    !(bits&8)?bound_current_run_raised:!(bits&16)?bound_current_run_resource_failure:
    bound_current_run_suspended;
  if(result!=expected)return 2;
  if((bits&7)==7 && c.observed!=c.revision)return 3;
  if(result==bound_current_run_suspended && (c.published!=18||c.effects!=2||!c.wake))return 4;
 }
 BoundClaimContext claim={.identity=true};
 if(bound_claim_run(&claim)!=bound_claim_run_done||claim.owned)return 5;
 if(bound_resume_run(&claim)!=bound_resume_run_done||claim.owned)return 6;
 return 0;
}
