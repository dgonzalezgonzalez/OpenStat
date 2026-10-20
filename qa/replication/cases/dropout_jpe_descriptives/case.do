* Replication case: Dropout-JPE descriptive statistics.
*
* Source: Arcidiacono, Aucejo, Maurel and Ransom, "College Attrition and the
* Dynamics of Information Revelation", Journal of Political Economy.  The
* package's own code is src/descriptives/descriptives.do; the logic below is
* the package's, reduced to the variables that the shipped data subset
* contains and instrumented with __OPENSTAT__ sentinels so the two engines
* can be compared numerically.
*
* The data is the package's own y97_all_tscrGPA.dta, reduced to the columns
* this script uses.

clear all
version 13.0
capture log close
set more off

use small.dta, clear

*=================================================
* Generate flags for various sample selection schemes
*=================================================
gen major_s=1 if (choice25==6 | choice25==7 | choice25==8 | choice25==9 | choice25==10 )
replace major_s=0 if (choice25==11 | choice25==12 | choice25==13 | choice25==14 | choice25==15 )

sort ID year
gen long obsno = _n
gen missing = missing(major_s)
bysort ID (missing obsno) : gen firstnonmissing_maj = major_s[1]

capture drop m_wage
capture drop m_GPA
capture drop m_maj
capture drop bad_wage
capture drop bad_grade
capture drop bad_major
capture drop sum_m_wage
capture drop sum_m_GPA
capture drop sum_maj
gen m_wage = anyFlag==0 & mi(log_comp) & (weeks_worked_Oct>=4) & inrange(avgHrsOct,10,.) & inlist(choice15,-2,1,2,4,5,7,8,10,11,13,14)
gen m_GPA  = anyFlag==0 & mi(GPA)      & (inlist(choice15,-2) | inrange(choice15,1,9))
gen m_maj  = anyFlag==0 & choice15==-2

sum m_wage if ~anyFlag
display "__OPENSTAT__n_m_wage=" %21.14g r(N)
display "__OPENSTAT__mean_m_wage=" %21.14g r(mean)
display "__OPENSTAT__sd_m_wage=" %21.14g r(sd)

bys ID (year): gen sum_m_wage = sum(m_wage)
bys ID (year): gen sum_m_GPA  = sum(m_GPA )
bys ID (year): gen sum_m_maj  = sum(m_maj )

gen bad_wage  = sum_m_wage>=1
gen bad_grade = sum_m_GPA >=2
gen bad_major = sum_m_maj >=2

count if anyFlag==0
display "__OPENSTAT__n_anyflag0=" %21.14g r(N)
count if anyFlag==0 & bad_major==0
display "__OPENSTAT__n_badmajor0=" %21.14g r(N)
count if anyFlag==0 & bad_major==0 & bad_wage==0
display "__OPENSTAT__n_badbw0=" %21.14g r(N)
count if anyFlag==0 & bad_major==0 & bad_wage==0 & bad_grade==0
display "__OPENSTAT__n_allok=" %21.14g r(N)

*=================================================
* Other variables of interest
*=================================================
gen HS_grades = Grades_HS_best
gen age18 = year<=2004
gen age19 = year==2005
gen age20 = year==2006
gen age21 = year==2007

gen y04 = year<=2004
gen y05 = year==2005
gen y06 = year==2006
gen y07 = year==2007
gen y08 = year==2008
gen y09 = year==2009
gen y10 = year==2010
gen y11 = year==2011
gen y12 = year==2012
gen y13 = year==2013
gen y14 = year==2014

forvalues x=1/3 {
gen cum_col_`x' = cum_college==`x'
gen cum_gs_`x'  = cum_grad_school==`x'
}
gen cum_col_4 = cum_college>=4 & !mi(cum_college)
gen cum_gs_4  = cum_grad_school>=4 & !mi(cum_grad_school)

gen yr2plus = cum_college>=2 & !mi(cum_college)

*=================================================
* Sample restrictions used in the paper
*=================================================
keep if male==1 & ~anyFlag & ~bad_wage & ~bad_grade & ~bad_major

count
display "__OPENSTAT__n_final=" %21.14g r(N)

summarize GPA
display "__OPENSTAT__gpa_mean=" %21.14g r(mean)
display "__OPENSTAT__gpa_sd=" %21.14g r(sd)
display "__OPENSTAT__gpa_min=" %21.14g r(min)
display "__OPENSTAT__gpa_max=" %21.14g r(max)

tabulate choice25
tabulate cum_col_4
tabulate yr2plus

summarize age18 age19 age20 age21
display "__OPENSTAT__age18=" %21.14g r(mean)
display "__OPENSTAT__age19=" %21.14g r(mean)
display "__OPENSTAT__age20=" %21.14g r(mean)
display "__OPENSTAT__age21=" %21.14g r(mean)

*=================================================
* Panel structure
*=================================================
bysort ID (year): gen n_year = _n
bysort ID: gen n_obs = _n
summarize n_obs
display "__OPENSTAT__n_obs_mean=" %21.14g r(mean)
display "__OPENSTAT__n_obs_min=" %21.14g r(min)
display "__OPENSTAT__n_obs_max=" %21.14g r(max)

count if n_obs==1
display "__OPENSTAT__n_singletons=" %21.14g r(N)

*=================================================
* Panel structure, as the package reports it
*=================================================
xtset ID year
xtsum ID
