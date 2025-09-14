// === НАЧАЛО КЛАССА PENDULUM PAINT SIMULATOR ДЛЯ РЕЖИМА РОЗЕТТЫ ===
class PendulumPaintSimulator {
    constructor() {
        // === НАЧАЛО ИНИЦИАЛИЗАЦИИ ПЕРЕМЕННЫХ ===
        this.canvas = document.getElementById('canvas');
        this.ctx = this.canvas.getContext('2d');
        
        this.svgPathData = '';
        this.time = 0;
        this.isRunning = false;
        this.pointsDrawn = 0;
        this.lastFrameTime = 0;
        this.fps = 0;
        
        this.maxAmplitude = 100;
        
        this.params = {
            canvasAmplitudeX: 55,
            canvasAmplitudeY: 55,
            canvasFreqX: 0.9,
            canvasFreqY: 1.1,
            bucketAmplitudeX: 65,
            bucketAmplitudeY: 65,
            bucketFreqX: 2.4,
            bucketFreqY: 1.8,
            damping: 0.0002,
            angularVelocity: 0.25,
            timeSpeed: 1.0,
            lineWidth: 1.2
        };
        
        this.lastX = null;
        this.lastY = null;
        // === КОНЕЦ ИНИЦИАЛИЗАЦИИ ПЕРЕМЕННЫХ ===

        this.initControls();
        this.initButtons();
        this.setupCanvas();
        this.animate();
    }
    
    // === НАЧАЛО МЕТОДОВ ИНИЦИАЛИЗАЦИИ ===
    initControls() {
        const params = this.params;
        Object.keys(params).forEach(param => {
            const slider = document.getElementById(param);
            const input = document.getElementById(param + 'Input');
            const valueDisplay = document.getElementById(param + 'Value');
            
            if (slider && valueDisplay) {
                slider.max = this.maxAmplitude;
                if (input) input.max = this.maxAmplitude;
                
                slider.value = params[param];
                valueDisplay.textContent = params[param];
                if (input) input.value = params[param];
                
                slider.addEventListener('input', (e) => {
                    params[param] = parseFloat(e.target.value);
                    valueDisplay.textContent = params[param];
                    if (input) input.value = params[param];
                });
            }
            
            if (input) {
                input.addEventListener('input', (e) => {
                    const value = parseFloat(e.target.value);
                    if (!isNaN(value)) {
                        const clampedValue = Math.min(Math.max(value, 0), this.maxAmplitude);
                        params[param] = clampedValue;
                        if (slider) slider.value = clampedValue;
                        if (valueDisplay) valueDisplay.textContent = clampedValue;
                        input.value = clampedValue;
                    }
                });
            }
        });
    }
    
    initButtons() {
        document.getElementById('startBtn').addEventListener('click', () => {
            this.isRunning = true;
        });
        
        document.getElementById('pauseBtn').addEventListener('click', () => {
            this.isRunning = false;
        });
        
        document.getElementById('clearBtn').addEventListener('click', () => {
            this.clearCanvas();
        });
        
        document.getElementById('saveBtn').addEventListener('click', () => {
            this.saveSVG();
        });
    }
    
    setupCanvas() {
        this.ctx.fillStyle = '#000';
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        this.ctx.strokeStyle = 'white';
        this.ctx.lineCap = 'round';
        this.ctx.lineJoin = 'round';
    }
    // === КОНЕЦ МЕТОДОВ ИНИЦИАЛИЗАЦИИ ===

    // === НАЧАЛО МЕТОДОВ РАСЧЕТА ПОЗИЦИИ ===
    calculatePosition(time) {
        return this.calculateRosettePosition(time);
    }
    
    calculateRosettePosition(time) {
        const centerX = this.canvas.width / 2;
        const centerY = this.canvas.height / 2;
        const p = this.params;
        
        const dampingFactor = Math.exp(-p.damping * time);
        const angleRad = p.angularVelocity * time;
        const cos = Math.cos(angleRad);
        const sin = Math.sin(angleRad);
        
        const canvasX = p.canvasAmplitudeX * dampingFactor * Math.sin(p.canvasFreqX * time);
        const canvasY = p.canvasAmplitudeY * dampingFactor * Math.cos(p.canvasFreqY * time);
        
        const bucketXRaw = p.bucketAmplitudeX * dampingFactor * Math.cos(p.bucketFreqX * time);
        const bucketYRaw = p.bucketAmplitudeY * dampingFactor * Math.sin(p.bucketFreqY * time);
        
        const bucketX = bucketXRaw * cos - bucketYRaw * sin;
        const bucketY = bucketXRaw * sin + bucketYRaw * cos;
        
        const x = centerX + (bucketX - canvasX);
        const y = centerY + (bucketY - canvasY);
        
        return { x, y, bucketX, bucketY, canvasX, canvasY };
    }
    // === КОНЕЦ МЕТОДОВ РАСЧЕТА ПОЗИЦИИ ===

    // === НАЧАЛО МЕТОДОВ ОТРИСОВКИ ===
    updatePendulumVisualization(bucketX, bucketY) {
        const rope = document.getElementById('rope');
        const bucket = document.getElementById('bucket');
        const drop = document.getElementById('drop');
        
        if (rope && bucket && drop) {
            const ropeX = 75 + bucketX * 0.2;
            const ropeY = 50 + bucketY * 0.15;
            
            rope.setAttribute('x2', ropeX);
            rope.setAttribute('y2', ropeY);
            bucket.setAttribute('x', ropeX - 10);
            bucket.setAttribute('y', ropeY - 5);
            drop.setAttribute('cx', ropeX);
            drop.setAttribute('cy', ropeY + 10);
        }
    }
    
    drawPoint(x, y) {
        this.drawRosettePoint(x, y);
    }
    
    drawRosettePoint(x, y) {
        const margin = 5;
        if (x < margin || x > this.canvas.width - margin || 
            y < margin || y > this.canvas.height - margin) {
            this.lastX = null;
            this.lastY = null;
            return;
        }
        
        this.ctx.lineWidth = this.params.lineWidth;
        
        if (this.lastX !== null && this.lastY !== null) {
            this.ctx.beginPath();
            this.ctx.moveTo(this.lastX, this.lastY);
            this.ctx.lineTo(x, y);
            this.ctx.stroke();
            
            this.svgPathData += ` L ${x.toFixed(2)} ${y.toFixed(2)}`;
        } else {
            this.svgPathData = `M ${x.toFixed(2)} ${y.toFixed(2)}`;
        }
        
        this.lastX = x;
        this.lastY = y;
        this.pointsDrawn++;
        
        this.updateDisplay();
    }
    
    updateDisplay() {
        document.getElementById('pointsCount').textContent = this.pointsDrawn;
        document.getElementById('pathLength').textContent = this.pointsDrawn;
        document.getElementById('positionDisplay').textContent = 'Центр';
    }
    // === КОНЕЦ МЕТОДОВ ОТРИСОВКИ ===

    // === НАЧАЛО МЕТОДОВ УПРАВЛЕНИЯ ===
    clearCanvas() {
        this.ctx.fillStyle = '#000';
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        
        this.svgPathData = '';
        this.lastX = null;
        this.lastY = null;
        this.pointsDrawn = 0;
        this.time = 0;
        
        document.getElementById('timeDisplay').textContent = '0.00';
        document.getElementById('pointsCount').textContent = '0';
        document.getElementById('pathLength').textContent = '0';
        document.getElementById('positionDisplay').textContent = 'Центр';
    }
    
    saveSVG() {
        if (!this.svgPathData) {
            alert('Нет данных для сохранения!');
            return;
        }
        
        const svgContent = `<?xml version="1.0" encoding="UTF-8"?>
<svg width="500" height="500" xmlns="http://www.w3.org/2000/svg">
  <path d="${this.svgPathData}" stroke="black" stroke-width="${this.params.lineWidth}" fill="none" 
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;
        
        const blob = new Blob([svgContent], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `guilloche-rosette-${Date.now()}.svg`;
        a.click();
        URL.revokeObjectURL(url);
    }
    // === КОНЕЦ МЕТОДОВ УПРАВЛЕНИЯ ===

    // === НАЧАЛО ГЛАВНОГО ЦИКЛА АНИМАЦИИ ===
    animate(currentTime = 0) {
        if (this.lastFrameTime) {
            this.fps = Math.round(1000 / (currentTime - this.lastFrameTime));
        }
        this.lastFrameTime = currentTime;
        
        if (this.isRunning) {
            this.time += 0.016 * this.params.timeSpeed;
            
            const pos = this.calculatePosition(this.time);
            this.updatePendulumVisualization(pos.bucketX, pos.bucketY);
            this.drawPoint(pos.x, pos.y);
            
            document.getElementById('timeDisplay').textContent = this.time.toFixed(2);
            document.getElementById('fpsDisplay').textContent = this.fps;
        }
        
        requestAnimationFrame((time) => this.animate(time));
    }
    // === КОНЕЦ ГЛАВНОГО ЦИКЛА АНИМАЦИИ ===
}

// === НАЧАЛО ИНИЦИАЛИЗАЦИИ ПРИЛОЖЕНИЯ ===
document.addEventListener('DOMContentLoaded', () => {
    new PendulumPaintSimulator();
});
// === КОНЕЦ ИНИЦИАЛИЗАЦИИ ПРИЛОЖЕНИЯ ===