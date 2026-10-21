* c10: macro expansion, r()/e()/c() semantics, the c() table
*
* The rules under test were measured against Stata 19.5 SE:
*   - `r(x)', `e(x)' and `c(x)' expand to *text* in macro position, the
*     value rendered with %18.0g and the padding removed;
*   - the same values in expression position are numbers, shown with %10.0g;
*   - an unknown name expands to nothing in macro position;
*   - an unknown r()/e() is the missing value in expression position;
*   - an unknown c() is the error "c(name) undefined" (rc 133).
clear all
set more off

* --- the macro expansion is text, not a number ------------------------
display "__OPENSTAT__cpi_macro=" "`c(pi)'"
display "__OPENSTAT__cpi_expr=" c(pi)

* --- an unknown name expands to nothing ------------------------------
display "__OPENSTAT__unknown_r_macro_len=" strlen("`r(nosuchthing)'")
display "__OPENSTAT__unknown_e_macro_len=" strlen("`e(nosuchthing)'")
display "__OPENSTAT__unknown_c_macro_len=" strlen("`c(nosuchthing)'")

* --- an unknown r()/e() is the missing value in expression position --
display "__OPENSTAT__unknown_r_expr=" r(nosuchthing)
display "__OPENSTAT__unknown_e_expr=" e(nosuchthing)

* --- an unknown c() is an error, rc 133 ------------------------------
capture display c(nosuchthing)
display "__OPENSTAT__rc_unknown_c=" %21.14g _rc

* --- c() names Stata 19.5 does not define stay undefined ------------
capture display c(SCS)
display "__OPENSTAT__rc_scs=" %21.14g _rc
capture display c(n_processors)
display "__OPENSTAT__rc_n_processors=" %21.14g _rc
capture display c(endian)
display "__OPENSTAT__rc_endian=" %21.14g _rc
capture display c(cmd)
display "__OPENSTAT__rc_cmd=" %21.14g _rc

* --- the c() values Stata does define -------------------------------
display "__OPENSTAT__c_maxint=" "`c(maxint)'"
display "__OPENSTAT__c_byteorder=" "`c(byteorder)'"
display "__OPENSTAT__c_version=" "`c(version)'"
display "__OPENSTAT__c_max_memory=" "`c(max_memory)'"

* --- r() values in the two positions --------------------------------
set obs 4
generate double x = 1
replace x = 2 in 2
replace x = 3 in 3
replace x = 6 in 4
summarize x
display "__OPENSTAT__rmean_macro=" "`r(mean)'"
display "__OPENSTAT__rmean_expr=" r(mean)
display "__OPENSTAT__rmean_text=" "`r(mean)'"

* --- a local keeps full precision even though its display rounds ----
local m = 0.585533385208193
display "__OPENSTAT__local_text=" "`m'"
display "__OPENSTAT__local_expr=" `m'
display "__OPENSTAT__local_len=" strlen("`m'")
