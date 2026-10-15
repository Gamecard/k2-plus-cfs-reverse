import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class ListFuncs extends GhidraScript {
    @Override
    public void run() throws Exception {
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        while (it.hasNext()) {
            Function f = it.next();
            println(String.format("%s %s size=%d", f.getEntryPoint(), f.getName(), (int)f.getBody().getNumAddresses()));
        }
        println("TOTAL: " + currentProgram.getFunctionManager().getFunctionCount());
    }
}
