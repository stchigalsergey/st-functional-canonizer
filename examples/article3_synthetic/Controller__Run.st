METHOD Run
output_signal := input_signal;
IF input_signal THEN
    memory := TRUE;
    delay(IN := input_signal, PT := T#5S);
ELSE
    memory := FALSE;
END_IF
END_METHOD
