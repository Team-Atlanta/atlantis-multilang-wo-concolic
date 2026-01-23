#include "FuzzerShm.h"
#include "manager.h"
#include "FuzzerTracePC.h"

#include <fcntl.h>
#include <semaphore.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/shm.h>
#include <unistd.h>

sem_t *StartSem = nullptr;
sem_t *EndSem = nullptr;
bool inited = false;
bool pass_wait = false;
struct InputManager* g_input_mgr = nullptr;
bool is_fuzzing_end = true;
bool is_crash = false;
char crash_logs[0x4000];
bool always_share = false;
bool testlang_feature = false;
bool silent_mode = false;
char* msg_in_wait = nullptr;

void WaitShm() {
  if (StartSem) {
    if(msg_in_wait) {
        fprintf(stdout, "\n%s\n", msg_in_wait);
        fprintf(stderr, "\n%s\n", msg_in_wait);
        fflush(stdout);
        fflush(stderr);
    }
    if (!g_current_worker->in_loop) {
      while (1) {
        if (sem_wait(StartSem) == 0)
          break;
      }
      g_current_worker->in_loop = 1;
    }
    pass_wait = true;
    testlang_feature = (g_current_worker->testlang_feature != 0);
  }
}

void EndShm(bool is_exited) {
  if (EndSem) {
    if (!is_exited) {
      g_current_worker->in_loop = 0;
      while (1) {
        if (sem_post(EndSem) == 0)
          break;
      }
    }
    pass_wait = false;
  }
}

void ExitShm() { EndShm(get_mode() == EXECUTE_INPUT); }

void AddResult(int Idx, unsigned int Result, bool new_normal_feature) {
  struct InputMetadata *md = get_input_metadata(g_input_mgr, Idx);
  if (md == nullptr)
    return;
  Coverage *cov = get_cov_buffer(g_input_mgr, Idx);
  if (cov == nullptr)
    return;
  int cov_size = fuzzer::TPC.StoreObservedPCs((uint64_t *)cov, COV_SIZE);
  md->cov_size = cov_size;
  md->result = Result;
  if (new_normal_feature) {
      md->new_normal_feature = 1;
  } else {
      md->new_normal_feature = 0;
  }
  if (Result != NORMAL) {
    CrashLog *dst_log = get_crash_log(g_input_mgr, Idx);
    if (dst_log == nullptr)
      return;
    int log_size = strlen(crash_logs);
    if (log_size > CRASH_LOG_SIZE) log_size = CRASH_LOG_SIZE;
    md->crash_size = log_size;
    memcpy(dst_log, crash_logs, log_size);
    crash_logs[0] = 0;
    if(is_fuzzing_end){
        ExitShm();
    }
    is_crash = true;
    is_fuzzing_end = true;
  }
}

bool ShareCorpus(const uint8_t *Data, size_t Size, unsigned int Result, bool new_normal_feature) {
  if (!pass_wait)
    return false;
  if (get_mode() == EXECUTE_INPUT) {
    AddResult(g_current_worker->execute_input_idx - 1, Result, new_normal_feature);
  } else {
    int idx = alloc_executed_input(g_input_mgr, Data, Size);
    if (idx < 0)
      return false;
    AddResult(idx, Result, new_normal_feature);
  }
  return true;
}

bool InitShm() {
  if (inited)
    return inited;
  msg_in_wait = getenv("UNIAFL_MSG_IN_WAIT");
  const char *sem_key = getenv("SEM_KEY");
  if (sem_key == NULL)
    return false;
  g_input_mgr = init_mgr(NULL, false);
  if(g_input_mgr == NULL)
      return false;
  if(getenv("ALWAYS_GET_COV") != NULL)
      always_share = true;

  char name[0x100];
  snprintf(name, sizeof(name), "%s.start", sem_key);
  StartSem = init_sem(name, false);
  snprintf(name, sizeof(name), "%s.end", sem_key);
  EndSem = init_sem(name, false);
  inited = StartSem != NULL && EndSem != NULL;
  return inited;
}
