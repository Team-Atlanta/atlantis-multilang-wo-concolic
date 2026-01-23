#include "FuzzerCorpus.h"

bool InitShm();
void WaitShm();
void EndShm(bool);

bool ShareCorpus(const uint8_t *, size_t, unsigned int Status, bool new_normal_feature);
extern struct InputManager* g_input_mgr;
extern bool is_fuzzing_end;
extern bool is_crash;
extern char crash_logs[0x4000];
extern bool always_share;
extern bool silent_mode;
extern bool testlang_feature;
