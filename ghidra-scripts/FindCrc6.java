import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.program.model.address.*;

public class FindCrc6 extends GhidraScript {
    @Override
    public void run() throws Exception {
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            if (!b.getName().equals(".text")) continue;
            AddressSet set = new AddressSet(b.getStart(), b.getEnd());
            // alles disassemblieren, auch unentdeckte Bereiche
            DisassembleCommand cmd = new DisassembleCommand(set, null, true);
            cmd.applyTo(currentProgram, monitor);
            println("Disassembliert: " + b.getName());
        }
        // jetzt scannen
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            if (!b.getName().equals(".text")) continue;
            AddressSetView set = new AddressSet(b.getStart(), b.getEnd());
            InstructionIterator ins = currentProgram.getListing().getInstructions(set, true);
            Address winStart = null;
            int shift = 0, eor = 0;
            while (ins.hasNext()) {
                Instruction in = ins.next();
                String m = in.getMnemonicString().toLowerCase();
                boolean s = m.startsWith("lsl") || m.startsWith("lsr");
                boolean e = m.startsWith("eor");
                if (s || e) {
                    if (winStart == null) winStart = in.getAddress();
                    if (s) shift++; else eor++;
                    if (in.getAddress().subtract(winStart) > 96) {
                        if (shift >= 2 && eor >= 2)
                            println(String.format("KANDIDAT @%s shift=%d eor=%d", winStart, shift, eor));
                        winStart = null; shift = 0; eor = 0;
                    }
                }
            }
            if (winStart != null && shift >= 2 && eor >= 2)
                println(String.format("KANDIDAT @%s shift=%d eor=%d", winStart, shift, eor));
        }
        println("SUCHE FERTIG");
    }
}
