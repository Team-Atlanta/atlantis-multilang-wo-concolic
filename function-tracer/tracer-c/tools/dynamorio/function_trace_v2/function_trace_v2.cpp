#include "dr_api.h"
#include "drmgr.h"
#include "drutil.h"
#include "drsyms.h"
#include "drwrap.h"

#include <string>
#include <iostream>
#include <fstream>
#include <unistd.h>
#include <cstring>
#include <stdint.h>
#include <map>
#include <vector>
#include <unordered_map>
#include <map>

static file_t gLogFile;

static void *gLock;
static void *gFunctionInfoCacheLock;
static app_pc main_start = NULL;
static app_pc main_end = NULL;

static void event_exit(void);

thread_id_t main_target_thread;

static module_data_t *g_main_module = NULL;
bool tracing = false;

#define MAX_FUNC_NAME_LEN 1024
#define MAX_FILE_PATH_LEN 1024

struct FunctionInfo {
    std::string function_name;
    std::string file_path;
    size_t line_number;
};

static std::unordered_map<thread_id_t, FunctionInfo> indirect_call_stats;
static std::map<app_pc, FunctionInfo> function_info_cache;


static FunctionInfo get_debug_info(app_pc addr) {
    FunctionInfo info;
    info.function_name = "UNKNOWN";
    info.file_path = "UNKNOWN";
    info.line_number = 0;

    dr_mutex_lock(gFunctionInfoCacheLock);
    if (function_info_cache.find(addr) != function_info_cache.end()) {
        info = function_info_cache[addr];
        dr_mutex_unlock(gFunctionInfoCacheLock);
        return info;
    }

    module_data_t *mod = dr_lookup_module(addr);
    if (mod == NULL) {
        info.function_name = "UNKNOWN_MODULE";
        return info;
    }
    
    char name[MAX_FUNC_NAME_LEN] = "UNKNOWN";
    char file_path[MAX_FILE_PATH_LEN] = "UNKNOWN";
    drsym_info_t sym_info;
    sym_info.struct_size = sizeof(sym_info);
    sym_info.name = name;
    sym_info.name_size = MAX_FUNC_NAME_LEN;
    sym_info.file = file_path;
    sym_info.file_size = MAX_FILE_PATH_LEN;
    sym_info.line = 0;
    
    drsym_error_t sym_res = drsym_lookup_address(
        mod->full_path, addr - mod->start, &sym_info, DRSYM_DEFAULT_FLAGS);
    
    if (sym_res == DRSYM_SUCCESS || sym_res == DRSYM_ERROR_LINE_NOT_AVAILABLE) {
        info.function_name = std::string(name);
        if (sym_info.file != NULL && strlen(sym_info.file) > 0) {
            info.file_path = std::string(file_path);
            info.line_number = sym_info.line;
        }
    }
    
    dr_free_module_data(mod);
    function_info_cache[addr] = info;
    dr_mutex_unlock(gFunctionInfoCacheLock);
    return info;
}

bool
is_instrumented_function(FunctionInfo info)
{
    if (strstr(info.file_path.c_str(), "llvm-project") != NULL) {
        return true;
    }
    if (strstr(info.file_path.c_str(), "compiler-rt") != NULL) {
        return true;
    }
    return false;
}

static std::string get_function_name(app_pc addr) {
    return get_debug_info(addr).function_name;
}

static dr_emit_flags_t
event_2nd_stage_analysis( void *drcontext, void *tag, instrlist_t *bb,
                        bool for_trace, bool translating, void **user_data);

static dr_emit_flags_t
event_3rd_stage_insertion( void *drcontext, void *tag, instrlist_t *bb,
                        instr_t *instr, bool for_trace, bool translating,
                        void *user_data);

DR_EXPORT void
dr_client_main(client_id_t id, int argc, const char *argv[])
{
    dr_set_client_name("Function Trace", "Team-Atlanta-sarif-tracer");
    if (argc != 2) {
        dr_printf("Usage: drrun -c function_trace.so [output_file_path] -- [run command]");
        return;
    }

    if (!drmgr_init()) {
        dr_printf("Failed to initialize drmgr\n");
        dr_close_file(gLogFile);
        return;
    }
    
    if (!drutil_init()) {
        dr_printf("Failed to initialize drutil\n"); 
        drmgr_exit();
        dr_close_file(gLogFile);
        return;
    }

    if (!drwrap_init()) {
        dr_printf("Failed to initialize drwrap\n");
        drutil_exit();
        drmgr_exit();
        dr_close_file(gLogFile);
        return;
    }

    drsym_error_t drsym_error = drsym_init(0);
    if (drsym_error != DRSYM_SUCCESS) {
        dr_printf("Failed to initialize drsyms: %d\n", drsym_error);
        drwrap_exit();
        drutil_exit();
        drmgr_exit();
        dr_close_file(gLogFile);
        return;
    }

    gLock = dr_mutex_create();
    gFunctionInfoCacheLock = dr_mutex_create();

    gLogFile = dr_open_file(argv[1], DR_FILE_WRITE_APPEND);
    DR_ASSERT(gLogFile != INVALID_FILE);

    if (!drmgr_register_bb_instrumentation_event(event_2nd_stage_analysis, 
                                                event_3rd_stage_insertion, NULL))
    {
        dr_printf("Failed to register bb instrumentation event\n");
        dr_close_file(gLogFile);
        return;
    }

    dr_register_exit_event(event_exit);

    return;
}

static void
event_exit(void)
{
    dr_close_file(gLogFile);

    dr_mutex_destroy(gLock);
    drsym_exit();
    drwrap_exit();
    drutil_exit();
    drmgr_exit();
}

static void direct_call_handler(void* drcontext, app_pc caller_addr, app_pc callee_addr)
{
    thread_id_t thread_id = dr_get_thread_id(drcontext);
    FunctionInfo caller_info = get_debug_info(caller_addr);
    FunctionInfo callee_info = get_debug_info(callee_addr);
    if (is_instrumented_function(caller_info) || is_instrumented_function(callee_info)) {
        return;
    }
    // dr_fprintf(STDERR, "DIRECT CALL: caller %s (%s:%d) -> target %s (%s:%d)\n", 
    //             caller_info.function_name.c_str(), 
    //             caller_info.file_path.c_str(), 
    //             caller_info.line_number,
    //             callee_info.function_name.c_str(), 
    //             callee_info.file_path.c_str(), 
    //             callee_info.line_number);
    if (caller_info.function_name != "UNKNOWN" 
        && callee_info.function_name != "UNKNOWN" 
        && caller_info.file_path != "UNKNOWN" 
        && callee_info.file_path != "UNKNOWN") 
    {
        dr_fprintf(gLogFile, "%d|-->|%s,%d,%s|-->|%s,%d,%s\n", 
                    thread_id,
                    caller_info.file_path.c_str(), 
                    caller_info.line_number, 
                    caller_info.function_name.c_str(), 
                    callee_info.file_path.c_str(), 
                    callee_info.line_number, 
                    callee_info.function_name.c_str());
    }
}

static void bb_first_instr_handler(void *drcontext, app_pc addr)
{   
    thread_id_t thread_id = dr_get_thread_id(drcontext);
    dr_mutex_lock(gLock);
    if (indirect_call_stats.find(thread_id) != indirect_call_stats.end()) {
        FunctionInfo caller_info = indirect_call_stats[thread_id];
        FunctionInfo callee_info = get_debug_info(addr);
        if (is_instrumented_function(caller_info) || is_instrumented_function(callee_info)) {
            indirect_call_stats.erase(thread_id);
            dr_mutex_unlock(gLock);
            return;
        }
        // dr_fprintf(STDERR, "INDIRECT CALL: caller %s (%s:%d) -> callee %s (%s:%d)\n", 
        //             caller_info.function_name.c_str(), 
        //             caller_info.file_path.c_str(), 
        //             caller_info.line_number,
        //             callee_info.function_name.c_str(), 
        //             callee_info.file_path.c_str(), 
        //             callee_info.line_number);
        if (caller_info.function_name != "UNKNOWN" 
            && callee_info.function_name != "UNKNOWN" 
            && caller_info.file_path != "UNKNOWN" 
            && callee_info.file_path != "UNKNOWN") 
        {
            dr_fprintf(gLogFile, "%d|-->|%s,%d,%s|-->|%s,%d,%s\n", 
                        thread_id,
                        caller_info.file_path.c_str(), 
                        caller_info.line_number, 
                        caller_info.function_name.c_str(), 
                        callee_info.file_path.c_str(), 
                        callee_info.line_number, 
                        callee_info.function_name.c_str());
        }

        indirect_call_stats.erase(thread_id);
    }
    dr_mutex_unlock(gLock);
}

static void indirect_call_handler(void* drcontext, app_pc caller_addr)
{
    dr_mutex_lock(gLock);
    FunctionInfo caller_info = get_debug_info(caller_addr);
    if (is_instrumented_function(caller_info)) {
        dr_mutex_unlock(gLock);
        return;
    }
    app_pc target_addr = dr_fragment_app_pc(drcontext);
    indirect_call_stats[dr_get_thread_id(drcontext)] = caller_info;
    dr_mutex_unlock(gLock);
}

static dr_emit_flags_t
event_2nd_stage_analysis( void *drcontext, void *tag, instrlist_t *bb,
                        bool for_trace, bool translating, void **user_data)
{
    return DR_EMIT_DEFAULT;
}

static dr_emit_flags_t
event_3rd_stage_insertion( void *drcontext, void *tag, instrlist_t *bb,
                        instr_t *instr, bool for_trace, bool translating,
                        void *user_data)
{
    if (instr_is_meta(instr)) return DR_EMIT_DEFAULT;

    if (!main_start) {
        module_data_t *main_module = dr_get_main_module();
        main_start = main_module->start;
        main_end = main_module->end;
        dr_free_module_data(main_module);
    }

    app_pc pc = instr_get_app_pc(instr);

    if (pc < main_start || pc > main_end) return DR_EMIT_DEFAULT;
    
    if (drmgr_is_first_instr(drcontext, instr)) {
        dr_insert_clean_call(drcontext, bb, instr,
                            (void*)bb_first_instr_handler,
                            false,
                            2, OPND_CREATE_INTPTR(drcontext), OPND_CREATE_INTPTR(pc));
    }
    
    if (instr_is_call(instr)) {
        if (instr_is_call_direct(instr)) {
            app_pc target = instr_get_branch_target_pc(instr);

            dr_insert_clean_call(drcontext, bb, instr,
                                (void*)direct_call_handler,
                                false,
                                3, 
                                OPND_CREATE_INTPTR(drcontext), 
                                OPND_CREATE_INTPTR(pc), 
                                OPND_CREATE_INTPTR(target));

        }
        else if (instr_is_call_indirect(instr)) {
            dr_insert_clean_call(drcontext, bb, instr,
                (void*)indirect_call_handler,
                false,
                2,
                OPND_CREATE_INTPTR(drcontext), OPND_CREATE_INTPTR(pc));
        }
    }
    
    return DR_EMIT_DEFAULT;
}
