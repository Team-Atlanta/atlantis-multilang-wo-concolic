/*
 * Copyright 2024 Code Intelligence GmbH
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.code_intelligence.jazzer.instrumentor;

import com.code_intelligence.jazzer.third_party.org.jacoco.core.internal.instr.InstrSupport;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;

public class StaticMethodStrategy implements EdgeCoverageStrategy {
  @Override
  public void instrumentControlFlowEdge(
      MethodVisitor mv, int edgeId, int variable, String coverageMapInternalClassName) {
    InstrSupport.push(mv, edgeId);
    mv.visitMethodInsn(
        Opcodes.INVOKESTATIC, coverageMapInternalClassName, "recordCoverage", "(I)V", false);
  }

  @Override
  public void instrumentControlFlowEdgeOnMethodEntry(
      MethodVisitor mv, String methodDesc, int variable, String coverageMapInternalClassName) {
    mv.visitLdcInsn(methodDesc);
    mv.visitMethodInsn(
        Opcodes.INVOKESTATIC, coverageMapInternalClassName, "recordCoverageOnMethodEntry", "(Ljava/lang/String;)V", false);
    
    System.out.println("[JAZZER-DEBUG] StaticMethodStrategy.instrumentControlFlowEdgeOnMethodEntry: methodDesc=" + methodDesc);
  }

  @Override
  public void instrumentControlFlowEdgeOnCallingMethod(
      MethodVisitor mv, String methodDesc, int variable, String coverageMapInternalClassName, String callerClassName, String callerMethodName, String callerMethodDesc, int callerLineNumber, String callerFileName) {
    mv.visitLdcInsn(methodDesc);
    mv.visitLdcInsn(callerClassName);
    mv.visitLdcInsn(callerMethodName);
    mv.visitLdcInsn(callerMethodDesc);
    InstrSupport.push(mv, callerLineNumber);
    mv.visitLdcInsn(callerFileName);
    mv.visitMethodInsn(
        Opcodes.INVOKESTATIC, coverageMapInternalClassName, "recordCoverageOnCallingMethod", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;ILjava/lang/String;)V", false);
    
    System.out.println("[JAZZER-DEBUG] StaticMethodStrategy.instrumentControlFlowEdgeOnCallingMethod: methodDesc=" + methodDesc);
  }


  @Override
  public int getInstrumentControlFlowEdgeStackSize() {
    return 1;
  }

  @Override
  public Object getLocalVariableType() {
    return null;
  }

  @Override
  public void loadLocalVariable(
      MethodVisitor mv, int variable, String coverageMapInternalClassName) {}

  @Override
  public int getLoadLocalVariableStackSize() {
    return 0;
  }
}
