function t(e){return e.includes(",")||e.includes('"')||e.includes(`
`)?'"'+e.replace(/"/g,'""')+'"':e}function p(e,c,s){const i="\uFEFF"+[c.map(t).join(","),...s.map(r=>r.map(t).join(","))].join(`
`),d=new Blob([i],{type:"text/csv;charset=utf-8;"}),o=URL.createObjectURL(d),n=document.createElement("a");n.href=o,n.download=e,n.style.display="none",document.body.appendChild(n),n.click(),document.body.removeChild(n),URL.revokeObjectURL(o)}export{p as e};
