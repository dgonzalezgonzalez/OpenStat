clear all
set more off
mata:
A = (1,2 \ 3,4)
B = A*A'
st_matrix("qa_matrix", B)
end
display "__OPENSTAT__m11=" %21.14g el(qa_matrix,1,1)
display "__OPENSTAT__m12=" %21.14g el(qa_matrix,1,2)
display "__OPENSTAT__m22=" %21.14g el(qa_matrix,2,2)
