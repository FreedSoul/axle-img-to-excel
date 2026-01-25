const API_URL = "INSERT_YOUR_API_URL_HERE"; // The user will find this in sam deploy output

const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const uploadBtn = document.getElementById('upload-btn');
const fileList = document.getElementById('file-list');

let filesToUpload = [];

// Drag and drop events
dropZone.onclick = () => fileInput.click();
dropZone.ondragover = (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
};
dropZone.ondragleave = () => dropZone.classList.remove('drag-over');
dropZone.ondrop = (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    handleFiles(e.dataTransfer.files);
};

fileInput.onchange = () => handleFiles(fileInput.files);

function handleFiles(files) {
    filesToUpload = Array.from(files);
    fileList.innerHTML = '';
    
    filesToUpload.forEach((file, index) => {
        const item = document.createElement('div');
        item.className = 'file-item';
        item.id = `file-${index}`;
        item.innerHTML = `
            <span>${file.name}</span>
            <span class="status status-waiting">Waiting</span>
        `;
        fileList.appendChild(item);
    });

    uploadBtn.disabled = filesToUpload.length === 0;
}

uploadBtn.onclick = async () => {
    uploadBtn.disabled = true;
    
    for (let i = 0; i < filesToUpload.length; i++) {
        const file = filesToUpload[i];
        const statusEl = document.querySelector(`#file-${i} .status`);
        
        try {
            updateStatus(statusEl, 'Requesting URL...', 'uploading');
            
            // 1. Get Pre-signed URL from API
            const response = await fetch(`${API_URL}/upload-url?file=${encodeURIComponent(file.name)}`);
            if (!response.ok) throw new Error('Failed to get upload URL');
            const { upload_url } = await response.json();
            
            updateStatus(statusEl, 'Uploading directly to S3...', 'uploading');

            // 2. Upload file directly to S3 using the URL
            const uploadResponse = await fetch(upload_url, {
                method: 'PUT',
                body: file,
                headers: {
                    'Content-Type': file.type
                }
            });

            if (uploadResponse.ok) {
                updateStatus(statusEl, 'Success!', 'status-success');
            } else {
                throw new Error('Upload failed');
            }
            
        } catch (error) {
            console.error(error);
            updateStatus(statusEl, 'Error', 'status-error');
        }
    }
    
    uploadBtn.disabled = false;
};

function updateStatus(el, text, className) {
    el.innerText = text;
    el.className = `status ${className}`;
}
