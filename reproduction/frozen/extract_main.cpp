#include <bits/stdc++.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
using namespace std;

static int col_index(const char* p, const char* end) {
    int n=0;
    while(p<end && *p>='A' && *p<='Z'){ n=n*26+(*p-'A'+1); ++p; }
    return n-1;
}
static string xml_unescape(string_view s){
    if(s.find('&')==string_view::npos) return string(s);
    string out; out.reserve(s.size());
    for(size_t i=0;i<s.size();){
        if(s[i]=='&'){
            if(s.substr(i,5)=="&amp;"){out.push_back('&');i+=5;}
            else if(s.substr(i,4)=="&lt;"){out.push_back('<');i+=4;}
            else if(s.substr(i,4)=="&gt;"){out.push_back('>');i+=4;}
            else if(s.substr(i,6)=="&quot;"){out.push_back('"');i+=6;}
            else if(s.substr(i,6)=="&apos;"){out.push_back('\'');i+=6;}
            else {out.push_back('&');++i;}
        } else out.push_back(s[i++]);
    }
    return out;
}
static const char* find_sv(const char* b,const char* e,string_view needle){
    auto it=std::search(b,e,needle.begin(),needle.end());
    return it==e?nullptr:it;
}
static string get_cell_value(const char* cb,const char* ce){
    const char* tb=find_sv(cb,ce,"<t");
    if(tb){
        const char* gt=(const char*)memchr(tb,'>',ce-tb);
        if(gt){ const char* te=find_sv(gt+1,ce,"</t>"); if(te) return xml_unescape(string_view(gt+1,te-(gt+1))); }
    }
    const char* vb=find_sv(cb,ce,"<v>");
    if(vb){ const char* ve=find_sv(vb+3,ce,"</v>"); if(ve) return string(vb+3,ve-(vb+3)); }
    return "";
}
int main(int argc,char**argv){
    if(argc<3){cerr<<"usage: extract_main xml out_tsv\n";return 2;}
    const char* path=argv[1]; const char* outpath=argv[2];
    int fd=open(path,O_RDONLY); if(fd<0){perror("open");return 1;}
    struct stat st; fstat(fd,&st); size_t n=st.st_size;
    char* data=(char*)mmap(nullptr,n,PROT_READ,MAP_PRIVATE,fd,0); if(data==MAP_FAILED){perror("mmap");return 1;}
    ofstream out(outpath); if(!out){cerr<<"bad out\n";return 1;}
    vector<int> wanted={0,1,2,3,4,5,8,11,12,13,14,15,16,17,18,19,20,21};
    unordered_set<int> want(wanted.begin(),wanted.end());
    out<<"row_id\tsource_file\tsource_period\tsource_row\toriginal_invoice_id\tsupplier_code\tbuyer_code\tseries\tnumber\tissue\tdue\tvalue\tremaining\tsupplier_validation\tbuyer_validation\tcanceled\tinvoice_status\tquality_flag\n";
    const char* p=find_sv(data,data+n,"<sheetData>"); if(!p){cerr<<"no sheetData\n";return 1;} p+=11;
    const char* end=data+n; size_t rows=0;
    while(true){
        const char* rb=find_sv(p,end,"<row"); if(!rb) break;
        const char* re=find_sv(rb,end,"</row>"); if(!re) break;
        re+=6;
        vector<string> vals(22);
        const char* q=rb;
        while(true){
            const char* cb=find_sv(q,re,"<c "); if(!cb) break;
            const char* ce=find_sv(cb,re,"</c>"); if(!ce) break;
            ce+=4;
            const char* rr=find_sv(cb,min(ce,cb+160),"r=\"");
            if(rr){ rr+=3; int ci=col_index(rr,ce); if(want.count(ci)) vals[ci]=get_cell_value(cb,ce); }
            q=ce;
        }
        if(rows>0){
            for(size_t i=0;i<wanted.size();++i){
                string &v=vals[wanted[i]];
                for(char &c:v) if(c=='\t'||c=='\n'||c=='\r') c=' ';
                if(i) out<<'\t'; out<<v;
            }
            out<<'\n';
        }
        ++rows;
        if(rows%100000==0) cerr<<"rows "<<rows<<"\n";
        p=re;
    }
    cerr<<"done rows including header "<<rows<<"\n";
    munmap(data,n); close(fd); return 0;
}
