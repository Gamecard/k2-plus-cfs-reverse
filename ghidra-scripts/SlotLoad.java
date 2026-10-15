import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import java.util.*;

public class SlotLoad extends GhidraScript {
    @Override
    public void run() throws Exception {
        Map<Long, Integer> buckets = new TreeMap<Long, Integer>();
        Map<Long, String> example = new TreeMap<Long, String>();
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            if (!b.getName().equals(".text")) continue;
            ghidra.program.model.address.AddressSetView set =
                new ghidra.program.model.address.AddressSet(b.getStart(), b.getEnd());
            InstructionIterator ins = currentProgram.getListing().getInstructions(set, true);
            while (ins.hasNext()) {
                Instruction in = ins.next();
                String m = in.getMnemonicString().toLowerCase();
                if (!m.startsWith("ldr")) continue;
                for (int i = 0; i < in.getNumOperands(); i++) {
                    for (Object o : in.getOpObjects(i)) {
                        if (o instanceof ghidra.program.model.scalar.Scalar) {
                            long v = ((ghidra.program.model.scalar.Scalar) o).getSignedValue();
                            if (v < -512 && v > -8192) {
                                buckets.put(v, buckets.getOrDefault(v, 0) + 1);
                                if (!example.containsKey(v))
                                    example.put(v, in.getAddress().toString());
                            }
                        }
                    }
                }
            }
        }
        // Top-Offsets nach Count
        List<Map.Entry<Long,Integer>> top = new ArrayList<Map.Entry<Long,Integer>>(buckets.entrySet());
        top.sort((x, y) -> y.getValue() - x.getValue());
        int n = 0;
        for (Map.Entry<Long,Integer> e : top) {
            if (e.getValue() < 5) break;
            println(String.format("OFFSET %d count=%d zB %s", e.getKey(), e.getValue(), example.get(e.getKey())));
            if (++n >= 15) break;
        }
        println("SUCHE FERTIG");
    }
}
