import { createContext, useContext } from "react";
import type { Mode } from "./viz";

export const ModeContext = createContext<Mode>("light");
export const useMode = () => useContext(ModeContext);
