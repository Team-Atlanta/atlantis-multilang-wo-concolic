#include <semaphore.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define COV_SIZE (1024 * 1024 / 8) // 1MB
#define CRASH_LOG_SIZE 16 * 1024 // 16KB

#define NO_SEED_ID 0xffffffffffffffff

// Mode
#define RUN_FUZZER 0
#define RUN_FUZZER_WITH_SEED 1
#define EXECUTE_INPUT 2

// Result
#define WAIT 0
#define NORMAL 1
#define CRASH 2
#define TIMEOUT 3
#define OOM 4

typedef uint64_t Coverage[COV_SIZE];
typedef char CrashLog[CRASH_LOG_SIZE];

struct InputMetadata {
  unsigned int input_size;
  unsigned int result;
  unsigned int cov_size;
  unsigned int crash_size;
  uint64_t id;    // id for seed, parent id for mutated seed
  uint64_t new_normal_feature; 
  char fname[16]; // might not be null terminated.
};

struct Worker {
  int start_input_idx;
  int end_input_idx;
  int alloc_input_idx;
  int execute_input_idx;

  int in_loop;
  unsigned int mode;
  int iter_cnt;
  unsigned int testlang_feature;
};

struct InputManager {
  struct Worker *workers;
  struct InputMetadata *input_metadatas;
  char *inputs;
  Coverage *coverages;
  CrashLog *crash_logs;
};

extern int g_input_per_worker;
extern int g_cur_worker_idx;
extern struct Worker *g_current_worker;

extern int g_ready_idx;
extern uint64_t g_cur_seed_id;
extern size_t max_input_size;
extern size_t aligned_input_size;

struct InputManager *init_mgr(const char *, bool);
sem_t *init_sem(const char *, bool);

int alloc_input(struct InputManager *, int);
int alloc_executed_input(struct InputManager *, const uint8_t *, size_t);

struct InputMetadata *get_input_metadata(struct InputManager *, int);
void set_input_metadata(struct InputManager *, int, unsigned int, uint64_t);
char *get_input_buffer(struct InputManager *, int);
unsigned int get_input_size(struct InputManager *, int);
Coverage *get_cov_buffer(struct InputManager *, int);
unsigned int get_cov_size(struct InputManager *, int);
CrashLog *get_crash_log(struct InputManager *, int);
unsigned int get_crash_size(struct InputManager *, int);
uint64_t get_id(struct InputManager *, int);
int get_result(struct InputManager *, int);
char *get_fname(struct InputManager *, int);

int get_mode();
void set_mode(struct InputManager *, int, int, bool);
void set_iter_cnt(struct InputManager *, int, int);
bool is_mode_ended(struct InputManager *, int);
bool consume_iter_cnt();

int get_start_input_idx(struct InputManager *, int);
int get_alloc_input_idx(struct InputManager *, int);
int get_execute_input_idx(struct InputManager *, int);
int get_seed_idx(struct InputManager *, int);
