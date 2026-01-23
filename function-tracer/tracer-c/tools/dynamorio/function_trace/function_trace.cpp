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

static file_t gLogFile;

static void *gLock;
static app_pc main_start = NULL;
static app_pc main_end = NULL;
bool sym_init = false;
bool module_load_event_triggered = false;

static void event_exit(void);
static void module_load_event(void *drcontext, const module_data_t *mod, bool loaded);
static void pre_func_callback(void *wrapcxt, void **user_data);
static void post_func_callback(void *wrapcxt, void *user_data);
static bool symbol_function_callback(const char *name, size_t offset, void *data);

thread_id_t main_target_thread;

static module_data_t *g_main_module = NULL;
bool tracing = false;
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

    g_main_module = dr_get_main_module();
    if (!g_main_module) {
        dr_printf("Failed to get main module\n");
        drsym_exit();
        drwrap_exit();
        drutil_exit();
        drmgr_exit();
        dr_close_file(gLogFile);
        return;
    }
    main_start = g_main_module->start;
    main_end = g_main_module->end;

    gLogFile = dr_open_file(argv[1], DR_FILE_WRITE_APPEND);
    DR_ASSERT(gLogFile != INVALID_FILE);

    if(!drmgr_register_module_load_event(module_load_event)) {
        dr_printf("Failed to register module load event\n");
        drsym_exit();
        drwrap_exit();
        drutil_exit();
        drmgr_exit();
        dr_close_file(gLogFile);
        return;
    }

    module_load_event(NULL, g_main_module, true);
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

static void
module_load_event(void *drcontext, const module_data_t *mod, bool loaded)
{
    dr_mutex_lock(gLock);
    if (module_load_event_triggered) {
        dr_mutex_unlock(gLock);
        return;
    }
    else {
        module_load_event_triggered = true;
    }
    dr_mutex_unlock(gLock);

    if (mod->start == main_start && mod->end == main_end) {
        dr_mutex_lock(gLock);
        dr_printf("Processing main module\n");
        drsym_error_t sym_error = drsym_enumerate_symbols(
            mod->full_path, symbol_function_callback, (void *)mod, DRSYM_DEMANGLE);
            
        if (sym_error != DRSYM_SUCCESS) {
            dr_printf("WARNING: Failed to enumerate symbols in main module: %d\n", sym_error);
        }
        dr_mutex_unlock(gLock);
        return;
    }
}

bool
is_function_symbol(size_t offset, module_data_t *mod)
{
    size_t memory_size;
    uint memory_prot;
    byte* base_pc;
    bool is_ok = dr_query_memory(mod->start + offset, &base_pc, &memory_size, &memory_prot);
    if(is_ok && (memory_prot & DR_MEMPROT_EXEC) != 0) {
        return true;
    }
    return false;
}

static bool
symbol_function_callback(const char *name, size_t offset, void *data)
{
    module_data_t *mod = (module_data_t *)data;
    
    // dr_printf("symbol_name_buf: %s\n", name);
    if (name != NULL) {
        // TODO: More generic way to check if the symbol is a function
        if (is_function_symbol(offset, mod)) {
            app_pc func_addr = (app_pc)(mod->start + offset);
            // dr_printf("Wrapping function: %s at %p\n", name, func_addr);
            drwrap_wrap(func_addr, pre_func_callback, post_func_callback);
        }
    }
    
    return true;
}

char *terminate_function_names[] = {
    "ExitCallback",
    "CrashCallback",
    "MaybeExitGracefully",
    "InterruptExitCode",
    "InterruptCallback",
    "AlarmCallback",
    "RssLimitCallback",
    NULL
};

bool
is_target_corrutine(char* function_name)
{
    if (strstr(function_name, "LLVMFuzzerTestOneInput")) {
        return true;
    }
    return false;
}

bool
is_fuzzer_terminate(char* function_name)
{
    int i = 0;
    if (strstr(function_name, "Fuzzer")) {
        for(i = 0; i < 7; i++) {
            if (strstr(function_name, terminate_function_names[i])) {
                return true;
            }
        }
    }
    return false;
}
static void
pre_func_callback(void *wrapcxt, void **user_data)
{
    app_pc func_addr = drwrap_get_func(wrapcxt);
    if (func_addr >= main_start && func_addr < main_end) {
        
        module_data_t *mod = dr_lookup_module(func_addr);
        if (mod != NULL) {
            size_t offset = func_addr - mod->start;
            drsym_info_t sym_info = {0};
            sym_info.struct_size = sizeof(sym_info);
            char name_buf[256] = {0};
            sym_info.name = name_buf;
            sym_info.name_size = sizeof(name_buf);
            
            // Add file and line information fields
            char file_buf[MAXIMUM_PATH] = {0};
            sym_info.file = file_buf;
            sym_info.file_size = sizeof(file_buf);
            sym_info.line = 0;
            sym_info.line_offs = 0;
            
            drsym_error_t sym_error = drsym_lookup_address(
                mod->full_path, offset, &sym_info, DRSYM_DEMANGLE | DRSYM_DEFAULT_FLAGS);
            
            // Get caller information
            app_pc caller_addr = NULL;
            char caller_name[256] = "unknown";
            char caller_file[MAXIMUM_PATH] = "unknown";
            int caller_line = -1;
            
            // Get caller address from the return address on the stack
            void *drcontext = drwrap_get_drcontext(wrapcxt);
            
            // Get thread ID
            thread_id_t thread_id = dr_get_thread_id(drcontext);
            
            dr_mcontext_t *mcontext = drwrap_get_mcontext(wrapcxt);
            bool got_context = (mcontext != NULL);
            
            if (got_context) {
                // On x86/x64, return address is typically on the stack
                // We can access it using the stack pointer register
                // #ifdef X86_64
                //     app_pc *stack_ptr = (app_pc *)mcontext.rsp;
                //     caller_addr = *stack_ptr;
                // #else
                //     app_pc *stack_ptr = (app_pc *)mcontext.esp;
                //     caller_addr = *stack_ptr;
                // #endif
                
                // NOTE: Assume target must be X86_64
                app_pc *stack_ptr = (app_pc *)mcontext->rsp;
                caller_addr = *stack_ptr;
                
                if (caller_addr != NULL) {
                    module_data_t *caller_mod = dr_lookup_module(caller_addr);
                    if (caller_mod != NULL) {
                        size_t caller_offset = caller_addr - caller_mod->start;
                        drsym_info_t caller_sym_info = {0};
                        caller_sym_info.struct_size = sizeof(caller_sym_info);
                        caller_sym_info.name = caller_name;
                        caller_sym_info.name_size = sizeof(caller_name);
                        caller_sym_info.file = caller_file;
                        caller_sym_info.file_size = sizeof(caller_file);
                        
                        drsym_error_t caller_error = drsym_lookup_address(
                            caller_mod->full_path, caller_offset, &caller_sym_info, 
                            DRSYM_DEMANGLE | DRSYM_DEFAULT_FLAGS);
                            
                        if (caller_error == DRSYM_SUCCESS && caller_name[0] != '\0') {
                            caller_line = caller_sym_info.line;
                        }
                        
                        dr_free_module_data(caller_mod);
                    }
                }
            }
            
            if (is_target_corrutine(name_buf)) {
                main_target_thread = thread_id;
                tracing = true;
            }
            if (is_fuzzer_terminate(name_buf)) {
                tracing = false;
                dr_exit_process(0);
            }
            if (tracing && (main_target_thread == thread_id) && !strstr(caller_file, "llvm-project") && !strstr(sym_info.file, "llvm-project")) {
                if (sym_error == DRSYM_SUCCESS && name_buf[0] != '\0') {
                    if (sym_info.line == 0) {
                        sym_info.line = -1;

                    } 
                    if (!strlen(file_buf)) {
                        strncpy(file_buf, "unknown", strlen("unknown"));
                    }
                    dr_fprintf(gLogFile, "%s,%d,%s|-->|%s,%d,%s\n", caller_file, caller_line, caller_name, sym_info.file, sym_info.line, name_buf);
                } else {
                    dr_fprintf(gLogFile, "%s,%d,%s|-->|unknown,-1,unknown\n", caller_file, caller_line, caller_name);
                }
            }
            
            dr_free_module_data(mod);
        } 
    }
}   

static void
post_func_callback(void *wrapcxt, void *user_data)
{
    app_pc func_addr = drwrap_get_func(wrapcxt);
    
    if (func_addr >= main_start && func_addr < main_end) {
        module_data_t *mod = dr_lookup_module(func_addr);
        if (mod != NULL) {
            void *drcontext = drwrap_get_drcontext(wrapcxt);
            
            // Get thread ID
            thread_id_t thread_id = dr_get_thread_id(drcontext);
            size_t offset = func_addr - mod->start;
            drsym_info_t sym_info = {0};
            sym_info.struct_size = sizeof(sym_info);
            char name_buf[256] = {0};
            sym_info.name = name_buf;
            sym_info.name_size = sizeof(name_buf);
            drsym_error_t sym_error = drsym_lookup_address(
                mod->full_path, offset, &sym_info, DRSYM_DEMANGLE | DRSYM_DEFAULT_FLAGS);

            // if (tracing && (main_target_thread == thread_id) && !strstr(sym_info.file, "llvm-project")) {        
            //     if (sym_error == DRSYM_SUCCESS && name_buf[0] != '\0') {
            //         dr_fprintf(gLogFile, "RET***%s\n", name_buf);
            //     } else {
            //         dr_fprintf(gLogFile, "RET***UNKNOWN\n");
            //     }
            // }
            if (is_target_corrutine(name_buf) && is_fuzzer_terminate(name_buf)) {
                tracing = false;
                dr_exit_process(0);
            }
            dr_free_module_data(mod);
        } 
    }
    return;
}

