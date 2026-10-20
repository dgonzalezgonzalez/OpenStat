* c08: error semantics - unknown names, unknown commands, error codes
clear all
set more off

capture confirm variable nosuchvar
display "__OPENSTAT__rc_confirm=" %21.14g _rc

capture generate y = nosuchvar
display "__OPENSTAT__rc_gen=" %21.14g _rc

capture display nosuchvar
display "__OPENSTAT__rc_display=" %21.14g _rc

capture nosuchcommand
display "__OPENSTAT__rc_command=" %21.14g _rc

capture summarize nosuchvar
display "__OPENSTAT__rc_summarize=" %21.14g _rc

capture confirm new variable nosuchvar
display "__OPENSTAT__rc_confirmnew=" %21.14g _rc

capture use "definitely-missing-file.dta"
display "__OPENSTAT__rc_use=" %21.14g _rc

* An unresolvable name inside display is reported with the partial output
* already on the line.
capture noisily display "prefix=" 1 " suffix=" nosuchvar
display "__OPENSTAT__rc_partial=" %21.14g _rc
