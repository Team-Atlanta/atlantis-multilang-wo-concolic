# **********************************************************
# DynamoRIO Line Trace Client
# **********************************************************

cmake_minimum_required(VERSION 3.7)

if ("${CMAKE_BUILD_TYPE}" MATCHES "Debug")
  set(DEBUG ON)
endif ("${CMAKE_BUILD_TYPE}" MATCHES "Debug")

project(function_trace_v2_v2)

set(output_dir "${PROJECT_BINARY_DIR}/bin")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${output_dir}")
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_LIBRARY_OUTPUT_DIRECTORY}")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${output_dir}")
if ("${CMAKE_GENERATOR}" MATCHES "Visual Studio")
  foreach (config ${CMAKE_CONFIGURATION_TYPES})
    string(TOUPPER "${config}" config_upper)
    set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_${config_upper}
      "${CMAKE_RUNTIME_OUTPUT_DIRECTORY}")
    set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY_${config_upper}
      "${CMAKE_ARCHIVE_OUTPUT_DIRECTORY}")
    set(CMAKE_LIBRARY_OUTPUT_DIRECTORY_${config_upper}
      "${CMAKE_LIBRARY_OUTPUT_DIRECTORY}")
  endforeach ()
endif ()

option(OPTIMIZE_CLIENT
  "optimizations (for inline performance)"
  ON)

if (WIN32)
  set(OPT_CFLAGS "/O2")
else (WIN32)
  set(OPT_CFLAGS "-O2")
endif (WIN32)

if (DEBUG)
  set(OPT_CFLAGS "${OPT_CFLAGS} -DDEBUG")
endif (DEBUG)

if (NOT DEFINED DynamoRIO_DIR)
  set(DynamoRIO_DIR "${DYNAMORIO_HOME}/cmake" CACHE PATH
    "DynamoRIO cmake directory")
endif (NOT DEFINED DynamoRIO_DIR)

find_package(DynamoRIO 11.3)
if (NOT DynamoRIO_FOUND)
  message(FATAL_ERROR "Need DynamoRIO package. please set DYNAMORIO_HOME")
endif(NOT DynamoRIO_FOUND)

if (WIN32)
  set(CL_CFLAGS "/GS- /wd4100 /wd4127 /wd4054")
  set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} ${CL_CFLAGS}")
  set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${CL_CFLAGS}")
  add_definitions(-D_CRT_SECURE_NO_WARNINGS)
endif (WIN32)

if (OPTIMIZE_CLIENT)
  set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} ${OPT_CFLAGS}")
  set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${OPT_CFLAGS}")
endif ()


configure_DynamoRIO_global(OFF ON)

add_library(function_trace_v2 SHARED function_trace_v2.cpp)
configure_DynamoRIO_client(function_trace_v2)

use_DynamoRIO_extension(function_trace_v2 drmgr)
use_DynamoRIO_extension(function_trace_v2 drutil)
use_DynamoRIO_extension(function_trace_v2 drsyms)
use_DynamoRIO_extension(function_trace_v2 drwrap)
if (NOT DynamoRIO_INTERNAL OR NOT "${CMAKE_GENERATOR}" MATCHES "Ninja")
  if ("${CMAKE_GENERATOR}" MATCHES "Visual Studio")
    if (DEBUG)
      set(location_suffix "_DEBUG")
    else ()
      set(location_suffix "_RELWITHDEBINFO")
    endif ()
  else ()
    set(location_suffix "")
  endif ()

  DynamoRIO_get_full_path(path function_trace_v2 "${location_suffix}")
  add_custom_command(TARGET function_trace_v2
    POST_BUILD
    COMMAND ${CMAKE_COMMAND}
    ARGS -E echo "Usage: $DRRUN_PATH -c ${path}"
    VERBATIM)
endif ()