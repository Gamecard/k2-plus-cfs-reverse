import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import java.util.*;

public class SlotFuncs extends GhidraScript {
    @Override
    public void run() throws Exception {
        long want = -4004;
        try { want = Long.parseLong(getScriptArgs().length > 0 ? getScriptArgs()[0] : "-4004"); } catch (Exception e) {}
        Set<String> funcs = new TreeSet<String>();
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            if (!b.getName().equals(".text")) continue;
            ghidra.program.model.address.AddressSetView set =
                new ghidra.program.model.address.AddressSet(b.getStart(), b.getEnd());
            InstructionIterator ins = currentProgram.getListing().getInstructions(set, true);
            while (ins.hasNext()) {
                Instruction in = ins.next();
                if (!in.getMnemonicString().toLowerCase().startsWith("ldr")) continue;
                for (int i = 0; i < in.getNumOperands(); i++) {
                    for (Object o : in.getOpObjects(i)) {
                        if (o instanceof ghidra.program.model.scalar.Scalar) {
                            if (((ghidra.program.model.scalar.Scalar) o).getSignedValue() == want) {
                                Function f = currentProgram.getFunctionManager()
                                    .getFunctionContaining(in.getAddress());
                                funcs.add(f == null ? in.getAddress().toString()
                                    : f.getEntryPoint() + " size=" + f.getBody().getNumAddresses());
                            }
                        }
                    }
                }
            }
        }
        println("OFFSET " + want + ": " + funcs.size() + " Funktionen");
        int n = 0;
        for (String f : funcs) { println("   " + f); if (++n >= 30) break; }
    }
}
