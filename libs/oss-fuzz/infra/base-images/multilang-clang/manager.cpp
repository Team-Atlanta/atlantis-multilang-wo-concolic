#include "manager.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/shm.h>
#include <sys/types.h>
#include <unistd.h>
#include <sys/stat.h>

int g_input_per_worker = 0;
int g_cur_worker_idx = -1;
struct Worker *g_current_worker = NULL;

int g_ready_idx = -1;
uint64_t g_cur_seed_id = NO_SEED_ID;

int g_capacity = 0;
int g_worker_cnt = 0;
size_t max_input_size = 0;
size_t aligned_input_size = 0;
#define PAGE_SIZE 4096
#define IS_INVALID_IDX(idx) (idx < 0 || idx >= g_capacity)
#define IS_INVALID_WORKER_IDX(idx) (idx < 0 || idx >= g_worker_cnt)

int create_shared_mem(const char *name, size_t size) {
  shm_unlink(name);
  int fd = shm_open(name, O_CREAT | O_RDWR, 0666);
  if (fd < 0)
    return -1;
  fchmod(fd, 0666);
  if (ftruncate(fd, size) < 0)
    return -1;
  return fd;
}

void *init_shared_mem(const char *name, const char *post, size_t size,
                      bool create) {
  int fd = -1;
  char mem_name[0x100];
  snprintf(mem_name, sizeof(mem_name), "%s.%s", name, post);
  if (create)
    fd = create_shared_mem(mem_name, size);
  else
    fd = shm_open(mem_name, O_RDWR, 0666);
  if (fd < 0) {
    return NULL;
  }
  void *ret = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  if (ret == MAP_FAILED)
    return NULL;
  return ret;
}

void init_cb(struct InputManager *mgr, const char *name, size_t worker_cnt,
             size_t capacity, bool create) {
  size_t size = sizeof(struct Worker) * worker_cnt +
                sizeof(struct InputMetadata) * capacity;
  uint8_t *block = (uint8_t *)init_shared_mem(name, "uniafl_cb", size, create);

  if (create) {
    memset(block, 0, size);
    for (size_t i = 0; i < worker_cnt; i++) {
      ((struct Worker *)block)[i].start_input_idx = i * g_input_per_worker;
      ((struct Worker *)block)[i].end_input_idx = (i + 1) * g_input_per_worker;
    }
  }
  mgr->workers = (struct Worker *)block;
  mgr->input_metadatas =
      (struct InputMetadata *)&block[sizeof(struct Worker) * worker_cnt];
}

sem_t *init_sem(const char *name, bool create) {
  if (create) {
    sem_t *ret = sem_open(name, O_CREAT, 0666, 0);
    char sem_path[1024];
    snprintf(sem_path, sizeof(sem_path), "/dev/shm/sem.%s", name);
    chmod(sem_path, 0666);
    return ret;
  } else {
    return sem_open(name, 0);
  }
}

struct InputManager *init_mgr(const char *name, bool create) {
  if (name == NULL)
    name = getenv("HARNESS_NAME");

  max_input_size = (size_t) atoi(getenv("MAX_INPUT_SIZE"));
  aligned_input_size = (max_input_size / PAGE_SIZE) * PAGE_SIZE;
  if ((max_input_size % PAGE_SIZE) != 0) {
      aligned_input_size += PAGE_SIZE;
  }

  struct InputManager *input_mgr =
      (struct InputManager *)malloc(sizeof(struct InputManager));
  g_input_per_worker = atoi(getenv("INPUT_PER_WORKER"));
  unsigned int worker_cnt = atoi(getenv("WORKER_CNT"));
  size_t capacity = g_input_per_worker * worker_cnt;
  g_capacity = capacity;
  g_worker_cnt = worker_cnt;

  init_cb(input_mgr, name, (size_t)worker_cnt, capacity, create);
  input_mgr->inputs = (char *)init_shared_mem(
      name, "uniafl_inputs", aligned_input_size * capacity, create);
  input_mgr->coverages = (Coverage *)init_shared_mem(
      name, "uniafl_coverage", sizeof(Coverage) * capacity, create);
  input_mgr->crash_logs = (CrashLog *)init_shared_mem(
      name, "uniafl_crash_log", sizeof(CrashLog) * capacity, create);
  const char *cur_worker = getenv("CUR_WORKER");
  if (cur_worker) {
    unsigned int cur_idx = atoi(cur_worker);
    if (cur_idx < worker_cnt) {
      g_cur_worker_idx = cur_idx;
      g_current_worker = &input_mgr->workers[cur_idx];
    } else {
      g_current_worker = NULL;
    }
  }

  if (input_mgr->workers != NULL && input_mgr->input_metadatas != NULL &&
      input_mgr->inputs != NULL && input_mgr->coverages != NULL) {
    return input_mgr;
  }
  return NULL;
}

int alloc_input(struct InputManager *input_mgr, int worker_idx) {
  if (IS_INVALID_WORKER_IDX(worker_idx))
    return -1;
  struct Worker *worker = &(input_mgr->workers[worker_idx]);
  int cur_idx = worker->alloc_input_idx;
  if (worker->end_input_idx > cur_idx) {
    memset(get_input_metadata(input_mgr, cur_idx), 0,
           sizeof(struct InputMetadata));
    worker->alloc_input_idx++;
    return cur_idx;
  }
  return -1;
}

int alloc_executed_input(struct InputManager *input_mgr, const uint8_t *data,
                         size_t size) {
  if (size > max_input_size) return -1;
  int cur_idx = g_current_worker->execute_input_idx;
  if (g_current_worker->end_input_idx > cur_idx) {
    struct InputMetadata *md = get_input_metadata(input_mgr, cur_idx);
    md->input_size = size;
    md->id = g_cur_seed_id;
    memcpy(get_input_buffer(input_mgr, cur_idx), data, size);
    g_current_worker->execute_input_idx++;
    return cur_idx;
  }
  return -1;
}

struct InputMetadata *get_input_metadata(struct InputManager *input_mgr,
                                         int idx) {
  if (IS_INVALID_IDX(idx))
    return NULL;
  return &(input_mgr->input_metadatas[idx]);
}

void set_input_metadata(struct InputManager *input_mgr, int idx,
                        unsigned int size, uint64_t id) {
  struct InputMetadata *md = get_input_metadata(input_mgr, idx);
  if (md == NULL)
    return;
  md->input_size = size;
  md->id = id;
  memset(&md->fname, 0, sizeof(md->fname));
}

char *get_input_buffer(struct InputManager *input_mgr, int idx) {
  if (IS_INVALID_IDX(idx))
    return NULL;
  return &(input_mgr->inputs[aligned_input_size * (size_t)idx]);
}

unsigned int get_input_size(struct InputManager *input_mgr, int idx) {
  struct InputMetadata *md = get_input_metadata(input_mgr, idx);
  if (md == NULL)
    return 0;
  return md->input_size;
}

Coverage *get_cov_buffer(struct InputManager *input_mgr, int idx) {
  if (IS_INVALID_IDX(idx))
    return NULL;
  return &(input_mgr->coverages[idx]);
}

unsigned int get_cov_size(struct InputManager *input_mgr, int idx) {
  struct InputMetadata *md = get_input_metadata(input_mgr, idx);
  if (md == NULL)
    return 0;
  return md->cov_size;
}

CrashLog *get_crash_log(struct InputManager *input_mgr, int idx) {
  if (IS_INVALID_IDX(idx))
    return NULL;
  return &(input_mgr->crash_logs[idx]);
}

unsigned int get_crash_size(struct InputManager *input_mgr, int idx) {
  struct InputMetadata *md = get_input_metadata(input_mgr, idx);
  if (md == NULL)
    return 0;
  return md->crash_size;
}

uint64_t get_id(struct InputManager *input_mgr, int idx) {
  struct InputMetadata *md = get_input_metadata(input_mgr, idx);
  if (md == NULL)
    return 0;
  return md->id;
}

int get_result(struct InputManager *input_mgr, int idx) {
  struct InputMetadata *md = get_input_metadata(input_mgr, idx);
  if (md == NULL)
    return -1;
  return md->result;
}

char *get_fname(struct InputManager *input_mgr, int idx) {
  struct InputMetadata *md = get_input_metadata(input_mgr, idx);
  if (md == NULL)
    return NULL;
  return md->fname;
}

int get_mode() {
  if (g_current_worker == NULL)
    return -1;
  return g_current_worker->mode;
}

void set_mode(struct InputManager *input_mgr, int worker_idx, int mode, bool testlang_feature) {
  if (IS_INVALID_WORKER_IDX(worker_idx))
    return;
  struct Worker *worker = &input_mgr->workers[worker_idx];
  worker->mode = mode;
  worker->iter_cnt = 0;
  worker->in_loop = 0;
  worker->alloc_input_idx = worker->start_input_idx;
  worker->execute_input_idx = worker->start_input_idx;
  if (testlang_feature) {
      worker->testlang_feature = 1;
  } else {
      worker->testlang_feature = 0;
  }
}

void set_iter_cnt(struct InputManager *input_mgr, int worker_idx, int cnt) {
  if (IS_INVALID_WORKER_IDX(worker_idx))
    return;
  struct Worker *worker = &input_mgr->workers[worker_idx];
  worker->iter_cnt = cnt;
}

bool is_mode_ended(struct InputManager *input_mgr, int worker_idx) {
  if (IS_INVALID_WORKER_IDX(worker_idx))
    return false;
  struct Worker *worker = &input_mgr->workers[worker_idx];
  switch (worker->mode) {
  case RUN_FUZZER:
  case RUN_FUZZER_WITH_SEED:
    return worker->iter_cnt <= 0 ||
           worker->execute_input_idx >= worker->end_input_idx;
  case EXECUTE_INPUT:
    return worker->execute_input_idx == worker->alloc_input_idx;
  }
  return false;
}

bool consume_iter_cnt() {
  if (g_current_worker->iter_cnt > 0) {
    g_current_worker->iter_cnt--;
    return true;
  }
  return false;
}

int get_start_input_idx(struct InputManager *input_mgr, int worker_idx) {
  if (IS_INVALID_WORKER_IDX(worker_idx))
    return -1;
  return input_mgr->workers[worker_idx].start_input_idx;
}

int get_alloc_input_idx(struct InputManager *input_mgr, int worker_idx) {
  if (IS_INVALID_WORKER_IDX(worker_idx))
    return -1;
  return input_mgr->workers[worker_idx].alloc_input_idx;
}

int get_execute_input_idx(struct InputManager *input_mgr, int worker_idx) {
  if (IS_INVALID_WORKER_IDX(worker_idx))
    return -1;
  return input_mgr->workers[worker_idx].execute_input_idx;
}

int get_seed_idx(struct InputManager *input_mgr, int worker_idx) {
  return get_start_input_idx(input_mgr, worker_idx);
}
